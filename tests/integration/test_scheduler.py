from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from safe_monitor.core.models import Event
from safe_monitor.core.scheduler import prune_fingerprints_job, retry_failed_events_job
from safe_monitor.publishers.base import Publisher
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


class _CapturingPublisher(Publisher):
    def __init__(self, ok: bool = True):
        self.ok = ok
        self.published: list[Event] = []

    async def publish(self, event: Event) -> bool:
        self.published.append(event)
        return self.ok


@pytest.mark.asyncio
async def test_retry_failed_events_publishes_and_deletes(tmp_path: Path):
    db = Database(tmp_path / "r.db")
    await db.init()

    # Insert an event_log row
    event_id = await db.log_event(
        source="fake_tg",
        received_at=datetime.now(UTC).isoformat(),
        published_at=None,
        severity="high",
        title="Retry me",
        url="https://example.com/x",
        raw_json="{}",
        filter_decision="publish_failed",
    )
    # failed_events with next_retry_at in the past
    past = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    await db.record_failed(event_id=event_id, error="boom", next_retry_at=past)

    pub = _CapturingPublisher(ok=True)
    processed = await retry_failed_events_job(db, pub)
    assert processed == 1
    assert len(pub.published) == 1
    assert pub.published[0].title == "Retry me"

    # failed_events row should be gone
    assert db._conn is not None
    async with db._conn.execute("SELECT COUNT(*) FROM failed_events") as cur:
        (count,) = await cur.fetchone()
    assert count == 0

    await db.close()


@pytest.mark.asyncio
async def test_retry_failed_events_backs_off_on_failure(tmp_path: Path):
    db = Database(tmp_path / "r2.db")
    await db.init()

    event_id = await db.log_event(
        source="fake_tg",
        received_at=datetime.now(UTC).isoformat(),
        published_at=None,
        severity="high",
        title="Still failing",
        url=None,
        raw_json="{}",
        filter_decision="publish_failed",
    )
    past = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    await db.record_failed(event_id=event_id, error="boom", next_retry_at=past)

    pub = _CapturingPublisher(ok=False)
    await retry_failed_events_job(db, pub)

    assert db._conn is not None
    async with db._conn.execute(
        "SELECT retry_count, next_retry_at FROM failed_events WHERE event_id = ?",
        (event_id,),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == 1
    # Rescheduled to the future
    assert datetime.fromisoformat(row[1]) > datetime.now(UTC)

    await db.close()
