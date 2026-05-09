from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._run_migrations()

    async def _run_migrations(self) -> None:
        assert self._conn is not None
        await self._conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)"
        )
        async with self._conn.execute("SELECT MAX(version) FROM schema_version") as cur:
            row = await cur.fetchone()
        current = row[0] if row and row[0] is not None else 0
        files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        for f in files:
            ver = int(f.stem.split("_", 1)[0])
            if ver <= current:
                continue
            sql = f.read_text()
            await self._conn.executescript(sql)
            await self._conn.execute(
                "INSERT OR REPLACE INTO schema_version(version) VALUES (?)", (ver,)
            )
            await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def check_and_insert_fingerprint(self, fingerprint: str, source: str) -> bool:
        """Return True if fingerprint already existed; else insert and return False."""
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT 1 FROM fingerprints WHERE fingerprint = ?", (fingerprint,)
        ) as cur:
            row = await cur.fetchone()
        if row is not None:
            await self._conn.execute(
                "UPDATE fingerprints SET event_count = event_count + 1 WHERE fingerprint = ?",
                (fingerprint,),
            )
            await self._conn.commit()
            return True
        await self._conn.execute(
            "INSERT INTO fingerprints(fingerprint, source) VALUES (?, ?)",
            (fingerprint, source),
        )
        await self._conn.commit()
        return False

    async def prune_old_fingerprints(self, older_than_days: int) -> int:
        assert self._conn is not None
        await self._conn.execute(
            "DELETE FROM fingerprints WHERE first_seen_at < datetime('now', ?)",
            (f"-{int(older_than_days)} days",),
        )
        await self._conn.commit()
        async with self._conn.execute("SELECT changes()") as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def set_checkpoint(self, source: str, kind: str, cursor: str) -> None:
        assert self._conn is not None
        await self._conn.execute(
            """INSERT INTO checkpoints(source, kind, cursor, updated_at)
               VALUES(?,?,?,?)
               ON CONFLICT(source) DO UPDATE SET
                 kind=excluded.kind, cursor=excluded.cursor, updated_at=excluded.updated_at""",
            (source, kind, cursor, datetime.now(UTC).isoformat()),
        )
        await self._conn.commit()

    async def get_checkpoint(self, source: str) -> dict[str, Any] | None:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT source, kind, cursor, updated_at FROM checkpoints WHERE source = ?",
            (source,),
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def log_event(
        self,
        *,
        source: str,
        received_at: str,
        published_at: str | None,
        severity: str,
        title: str,
        url: str | None,
        raw_json: str,
        filter_decision: str,
    ) -> int:
        assert self._conn is not None
        cur = await self._conn.execute(
            """INSERT INTO event_log(source, received_at, published_at,
                 severity, title, url, raw_json, filter_decision)
               VALUES (?,?,?,?,?,?,?,?)""",
            (source, received_at, published_at, severity, title, url, raw_json, filter_decision),
        )
        await self._conn.commit()
        return int(cur.lastrowid or 0)

    async def record_failed(self, event_id: int, error: str, next_retry_at: str) -> None:
        assert self._conn is not None
        await self._conn.execute(
            """INSERT INTO failed_events(event_id, last_error, retry_count, next_retry_at)
               VALUES(?, ?, 0, ?)""",
            (event_id, error, next_retry_at),
        )
        await self._conn.commit()

    async def get_pending_retries(self, now_iso: str) -> list[dict[str, Any]]:
        """Return failed_events rows where next_retry_at <= now and retry_count < 5."""
        assert self._conn is not None
        async with self._conn.execute(
            """SELECT id, event_id, last_error, retry_count, next_retry_at
               FROM failed_events
               WHERE next_retry_at <= ? AND retry_count < 5
               ORDER BY next_retry_at ASC""",
            (now_iso,),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def update_retry(self, id: int, next_retry_at: str, increment: bool = True) -> None:
        """Update next_retry_at and (optionally) bump retry_count by 1."""
        assert self._conn is not None
        if increment:
            await self._conn.execute(
                """UPDATE failed_events
                   SET next_retry_at = ?, retry_count = retry_count + 1
                   WHERE id = ?""",
                (next_retry_at, id),
            )
        else:
            await self._conn.execute(
                "UPDATE failed_events SET next_retry_at = ? WHERE id = ?",
                (next_retry_at, id),
            )
        await self._conn.commit()

    async def delete_failed(self, id: int) -> None:
        assert self._conn is not None
        await self._conn.execute("DELETE FROM failed_events WHERE id = ?", (id,))
        await self._conn.commit()

    async def get_event_by_id(self, event_id: int) -> dict[str, Any] | None:
        assert self._conn is not None
        async with self._conn.execute(
            """SELECT id, source, received_at, published_at, severity,
                      title, url, raw_json, filter_decision
               FROM event_log WHERE id = ?""",
            (event_id,),
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def upsert_x_user(self, *, handle: str, user_id: str, tier: str) -> None:
        assert self._conn is not None
        await self._conn.execute(
            """INSERT INTO x_users(user_id, handle, tier, updated_at)
               VALUES(?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                 handle=excluded.handle, tier=excluded.tier,
                 updated_at=excluded.updated_at""",
            (user_id, handle, tier, datetime.now(UTC).isoformat()),
        )
        await self._conn.commit()

    async def list_x_users(self) -> list[dict[str, Any]]:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT user_id, handle, tier, last_seen_id FROM x_users ORDER BY handle"
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def set_x_user_last_seen(self, user_id: str, last_seen_id: str) -> None:
        assert self._conn is not None
        await self._conn.execute(
            "UPDATE x_users SET last_seen_id=?, updated_at=? WHERE user_id=?",
            (last_seen_id, datetime.now(UTC).isoformat(), user_id),
        )
        await self._conn.commit()

    async def set_degraded(self, source: str, degraded: bool, reason: str | None = None) -> None:
        assert self._conn is not None
        await self._conn.execute(
            """INSERT INTO source_status(source, degraded, reason, changed_at)
               VALUES(?,?,?,?)
               ON CONFLICT(source) DO UPDATE SET
                 degraded=excluded.degraded, reason=excluded.reason,
                 changed_at=excluded.changed_at""",
            (source, 1 if degraded else 0, reason, datetime.now(UTC).isoformat()),
        )
        await self._conn.commit()

    async def is_degraded(self, source: str) -> bool:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT degraded FROM source_status WHERE source=?", (source,)
        ) as cur:
            row = await cur.fetchone()
        return bool(row and row[0])
