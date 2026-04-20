from datetime import datetime, timezone
from pathlib import Path

import pytest

from safe_monitor.storage.db import Database


@pytest.fixture
async def db(tmp_path: Path):
    d = Database(tmp_path / "t.db")
    await d.init()
    yield d
    await d.close()


async def test_fingerprint_insert_and_hit(db: Database):
    fp = "abcd1234"
    existed = await db.check_and_insert_fingerprint(fp, source="x")
    assert existed is False
    existed = await db.check_and_insert_fingerprint(fp, source="x")
    assert existed is True


async def test_checkpoint_roundtrip(db: Database):
    await db.set_checkpoint("defillama_api", kind="api_poll", cursor="2026-04-20T00:00:00Z")
    row = await db.get_checkpoint("defillama_api")
    assert row["cursor"] == "2026-04-20T00:00:00Z"
    assert row["kind"] == "api_poll"


async def test_event_log_append(db: Database):
    now = datetime.now(timezone.utc).isoformat()
    eid = await db.log_event(
        source="defillama_api",
        received_at=now,
        published_at=None,
        severity="high",
        title="test",
        url="https://x",
        raw_json="{}",
        filter_decision="published",
    )
    assert eid > 0
