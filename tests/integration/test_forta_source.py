import asyncio
import json
from pathlib import Path

import pytest
import respx
from httpx import Response

from safe_monitor.sources.forta import FortaPoller
from safe_monitor.storage.db import Database


@pytest.mark.asyncio
async def test_forta_first_run_seeds_without_emitting(tmp_path: Path):
    db = Database(tmp_path / "f.db")
    await db.init()
    body = Path("tests/fixtures/forta_alerts_response.json").read_text()

    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://api.forta.network/graphql").mock(
            return_value=Response(200, text=body)
        )
        poller = FortaPoller(
            name="forta",
            endpoint="https://api.forta.network/graphql",
            poll_interval_seconds=60,
            db=db,
        )
        q: asyncio.Queue = asyncio.Queue()
        await poller.poll_once(q)

    assert q.empty()  # first run seeds, emits nothing

    cp = await db.get_checkpoint("forta")
    assert cp is not None
    assert int(cp["cursor"]) > 0
    await db.close()


@pytest.mark.asyncio
async def test_forta_second_run_emits_new_alerts(tmp_path: Path):
    db = Database(tmp_path / "f.db")
    await db.init()
    body = Path("tests/fixtures/forta_alerts_response.json").read_text()

    poller = FortaPoller(
        name="forta",
        endpoint="https://api.forta.network/graphql",
        poll_interval_seconds=60,
        db=db,
    )
    q: asyncio.Queue = asyncio.Queue()

    # Seed with an old cursor so the fixture alerts are "newer".
    await db.set_checkpoint("forta", kind="api_poll", cursor="1000")

    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://api.forta.network/graphql").mock(
            return_value=Response(200, text=body)
        )
        await poller.poll_once(q)

    got = []
    while not q.empty():
        got.append(await q.get())

    assert len(got) == 2
    hashes = {e.raw["hash"] for e in got}
    assert "0x9ab5c1a0d3e4f5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0" in hashes

    # Third run with same body → cursor advanced, no new emissions.
    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://api.forta.network/graphql").mock(
            return_value=Response(200, text=body)
        )
        await poller.poll_once(q)
    assert q.empty()
    await db.close()


@pytest.mark.asyncio
async def test_forta_handles_graphql_errors(tmp_path: Path):
    db = Database(tmp_path / "f.db")
    await db.init()
    err = json.dumps({"errors": [{"message": "rate limited"}]})

    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://api.forta.network/graphql").mock(
            return_value=Response(200, text=err)
        )
        poller = FortaPoller(
            name="forta",
            endpoint="https://api.forta.network/graphql",
            poll_interval_seconds=60,
            db=db,
        )
        q: asyncio.Queue = asyncio.Queue()
        await poller.poll_once(q)

    assert q.empty()
    await db.close()
