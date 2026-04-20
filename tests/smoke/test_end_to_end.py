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


class FakeTG:
    name = "peckshield_tg"

    def __init__(self, texts: list[str]):
        self._texts = texts

    async def run(self, sink: asyncio.Queue):
        for i, t in enumerate(self._texts):
            await sink.put(
                RawEvent(
                    source="peckshield_tg",
                    source_kind="tg",
                    external_id=str(i),
                    received_at=datetime.now(UTC),
                    raw={"text": t},
                    text=t,
                )
            )
        await asyncio.sleep(3600)


class Collector(Publisher):
    def __init__(self):
        self.events: list[Event] = []

    async def publish(self, e: Event) -> bool:
        self.events.append(e)
        return True


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_smoke_pipeline(tmp_path: Path):
    db = Database(tmp_path / "smoke.db")
    await db.init()
    texts = [
        "Resolv protocol exploited for $80M on Ethereum. Tx: 0x" + "c" * 64,
        "Resolv protocol exploited for $80M on Ethereum. Tx: 0x" + "c" * 64,  # dup
        "Free airdrop — claim now!",  # denied by keyword
        "Another protocol drained $2M",  # should pass
    ]
    pub = Collector()
    orch = Orchestrator(
        sources=[FakeTG(texts)],
        publisher=pub,
        db=db,
        filter_=Filter(FilterCfg(min_severity="medium", deny_keywords=["airdrop"])),
    )
    task = asyncio.create_task(orch.run())
    for _ in range(100):
        await asyncio.sleep(0.05)
        if len(pub.events) >= 2:
            break
    orch.request_shutdown()
    await asyncio.wait_for(task, timeout=5)
    assert len(pub.events) == 2
    titles = [e.title for e in pub.events]
    assert any("Resolv" in t for t in titles)
    assert any("Another protocol" in t for t in titles)
    await db.close()
