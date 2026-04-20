import asyncio
import json
from pathlib import Path

import pytest
import respx
from httpx import Response

from safe_monitor.sources.defillama import DefiLlamaHacksPoller
from safe_monitor.storage.db import Database


@pytest.mark.asyncio
async def test_poller_emits_only_new_events(tmp_path: Path):
    db = Database(tmp_path / "d.db")
    await db.init()
    fixture = json.loads(Path("tests/fixtures/defillama_hacks_response.json").read_text())

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.llama.fi/hacks").mock(return_value=Response(200, json=fixture))

        queue: asyncio.Queue = asyncio.Queue()
        poller = DefiLlamaHacksPoller(
            name="defillama_api",
            endpoint="https://api.llama.fi/hacks",
            poll_interval_seconds=1,
            db=db,
        )
        # run one cycle manually
        await poller.poll_once(queue)

    received = []
    while not queue.empty():
        received.append(await queue.get())

    # Only entries newer than epoch 0 are emitted on first run (first run seeds all)
    assert len(received) == 2
    assert received[1].raw["name"] == "Fresh Hack"

    # Second cycle should emit nothing new (checkpoint saved)
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.llama.fi/hacks").mock(return_value=Response(200, json=fixture))
        await poller.poll_once(queue)
    assert queue.empty()
    await db.close()
