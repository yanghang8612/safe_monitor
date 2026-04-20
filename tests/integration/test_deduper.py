from datetime import datetime, timezone
from pathlib import Path

from safe_monitor.core.deduper import Deduper
from safe_monitor.core.models import Event, EventCategory, Severity
from safe_monitor.storage.db import Database


def _event(fp: str = "fp1") -> Event:
    return Event(
        fingerprint=fp,
        source="x",
        title="t",
        severity=Severity.high,
        category=[EventCategory.a],
        received_at=datetime.now(timezone.utc),
        raw={},
    )


async def test_first_time_passes(tmp_path: Path):
    db = Database(tmp_path / "d.db")
    await db.init()
    d = Deduper(db)
    assert await d.is_duplicate(_event()) is False
    assert await d.is_duplicate(_event()) is True
    await db.close()
