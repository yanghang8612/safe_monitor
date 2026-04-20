import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from safe_monitor.config import FilterCfg
from safe_monitor.core.filter import Filter
from safe_monitor.core.models import Event, RawEvent
from safe_monitor.core.orchestrator import Orchestrator
from safe_monitor.publishers.base import Publisher
from safe_monitor.storage.db import Database


class FakeSource:
    name = "fake_tg"

    def __init__(self, items: list[RawEvent]):
        self._items = items

    async def run(self, sink: asyncio.Queue):
        for it in self._items:
            await sink.put(it)
        # sleep forever; orchestrator will cancel us
        await asyncio.sleep(3600)


class CapturingPublisher(Publisher):
    def __init__(self):
        self.published: list[Event] = []

    async def publish(self, event: Event) -> bool:
        self.published.append(event)
        return True


@pytest.mark.asyncio
async def test_orchestrator_dedups_and_publishes(tmp_path: Path):
    db = Database(tmp_path / "o.db")
    await db.init()
    raw_a = RawEvent(
        source="fake_tg",
        source_kind="tg",
        external_id="1",
        received_at=datetime.now(UTC),
        raw={"text": "x"},
        text="ProtocolX exploited loss $5M",
    )
    raw_b_dup = raw_a.model_copy(update={"external_id": "2"})  # same text → same fp
    raw_c = raw_a.model_copy(
        update={
            "external_id": "3",
            "text": "OtherProto drained $20M",
            "raw": {"text": "OtherProto drained $20M"},
        }
    )

    pub = CapturingPublisher()
    orch = Orchestrator(
        sources=[FakeSource([raw_a, raw_b_dup, raw_c])],
        publisher=pub,
        db=db,
        filter_=Filter(FilterCfg(min_severity="medium", deny_keywords=[])),
    )

    async def _runner():
        await orch.run()

    task = asyncio.create_task(_runner())
    # Wait for the 3 raws to be handled
    for _ in range(50):
        await asyncio.sleep(0.05)
        if len(pub.published) >= 2:
            break
    orch.request_shutdown()
    await asyncio.wait_for(task, timeout=5)

    # raw_a and raw_c published; raw_b_dup deduped
    assert len(pub.published) == 2
    await db.close()
