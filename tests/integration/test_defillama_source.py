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

    # --- First run: should seed checkpoint and emit nothing ---
    # No HTTP should be issued (we return early before fetching); respx
    # with assert_all_called would fail if we registered a route.
    queue: asyncio.Queue = asyncio.Queue()
    poller = DefiLlamaHacksPoller(
        name="defillama_api",
        endpoint="https://api.llama.fi/hacks",
        poll_interval_seconds=1,
        db=db,
    )
    await poller.poll_once(queue)
    assert queue.empty()

    cp = await db.get_checkpoint("defillama_api")
    assert cp is not None
    assert cp["cursor"] is not None
    # Checkpoint was seeded to ~now
    assert int(cp["cursor"]) > 1_700_000_000

    # --- Seed checkpoint to 1_700_000_000 so only "Fresh Hack" (1715000000)
    # is newer; "Old Hack" (1600000000) is older.
    await db.set_checkpoint("defillama_api", kind="api_poll", cursor="1700000000")

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.llama.fi/hacks").mock(return_value=Response(200, json=fixture))
        await poller.poll_once(queue)

    received = []
    while not queue.empty():
        received.append(await queue.get())
    assert len(received) == 1
    assert received[0].raw["name"] == "Fresh Hack"

    # --- Third cycle: checkpoint has advanced past Fresh Hack → no new emissions ---
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.llama.fi/hacks").mock(return_value=Response(200, json=fixture))
        await poller.poll_once(queue)
    assert queue.empty()
    await db.close()
