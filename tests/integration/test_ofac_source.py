import asyncio
from pathlib import Path

import pytest
import respx
from httpx import Response

from safe_monitor.sources.ofac import OfacSdnPoller
from safe_monitor.storage.db import Database


@pytest.mark.asyncio
async def test_ofac_poller_emits_addresses(tmp_path: Path):
    db = Database(tmp_path / "o.db")
    await db.init()
    xml = Path("tests/fixtures/ofac_sdn_sample.xml").read_text()

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://example/ofac.xml").mock(return_value=Response(200, text=xml))
        poller = OfacSdnPoller(
            name="ofac_sdn",
            endpoint="https://example/ofac.xml",
            poll_interval_seconds=1,
            db=db,
        )
        q: asyncio.Queue = asyncio.Queue()
        await poller.poll_once(q)

    got = []
    while not q.empty():
        got.append(await q.get())

    assert len(got) == 1
    assert "0xabcdef" in got[0].text.lower()

    # Second poll: same content → no new emissions
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://example/ofac.xml").mock(return_value=Response(200, text=xml))
        await poller.poll_once(q)
    assert q.empty()
    await db.close()
