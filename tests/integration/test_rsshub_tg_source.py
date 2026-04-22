import asyncio
from pathlib import Path

import pytest
import respx
from httpx import Response

from safe_monitor.sources.rsshub_tg import RSSHubTGPoller
from safe_monitor.storage.db import Database

FIXTURE = Path(__file__).parent.parent / "fixtures" / "rsshub_tg_sample.xml"


@pytest.mark.asyncio
async def test_rsshub_poller_emits_new_items(tmp_path: Path):
    db = Database(tmp_path / "r.db")
    await db.init()
    xml = FIXTURE.read_text()

    poller = RSSHubTGPoller(
        name="peckshield_tg",
        username="peckshield",
        rsshub_base_url="https://rsshub.example",
        poll_interval_seconds=1,
        db=db,
    )

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://rsshub.example/telegram/channel/peckshield").mock(
            return_value=Response(200, text=xml)
        )
        q: asyncio.Queue = asyncio.Queue()
        await poller.poll_once(q)

    received = []
    while not q.empty():
        received.append(await q.get())

    # First run has no checkpoint => emit both items
    assert len(received) == 2
    # Most recent item must contain Resolv text (HTML stripped)
    texts = [r.text for r in received]
    assert any("Resolv" in t and "80M" in t for t in texts)
    assert all("<p>" not in (t or "") for t in texts)

    # Second run: checkpoint should suppress everything
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://rsshub.example/telegram/channel/peckshield").mock(
            return_value=Response(200, text=xml)
        )
        await poller.poll_once(q)
    assert q.empty()

    await db.close()
