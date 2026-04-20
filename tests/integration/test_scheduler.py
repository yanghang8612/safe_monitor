from pathlib import Path

import pytest

from safe_monitor.core.scheduler import prune_fingerprints_job
from safe_monitor.storage.db import Database


@pytest.mark.asyncio
async def test_prune_deletes_old(tmp_path: Path):
    db = Database(tmp_path / "s.db")
    await db.init()
    # insert a fingerprint with old timestamp
    assert db._conn is not None
    await db._conn.execute(
        "INSERT INTO fingerprints(fingerprint, source, first_seen_at) VALUES (?,?,?)",
        ("old", "x", "2020-01-01 00:00:00"),
    )
    await db._conn.execute(
        "INSERT INTO fingerprints(fingerprint, source) VALUES (?,?)",
        ("new", "x"),
    )
    await db._conn.commit()

    deleted = await prune_fingerprints_job(db, ttl_days=7)
    assert deleted == 1

    async with db._conn.execute("SELECT fingerprint FROM fingerprints") as cur:
        rows = [r[0] for r in await cur.fetchall()]
    assert rows == ["new"]

    await db.close()
