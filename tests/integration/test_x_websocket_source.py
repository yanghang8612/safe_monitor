import asyncio
import json
from pathlib import Path

import pytest
import websockets

from safe_monitor.sources.x_websocket import XWebSocketSource
from safe_monitor.storage.db import Database


async def _start_mock_ws(handler):
    return await websockets.serve(handler, "127.0.0.1", 0)


@pytest.mark.asyncio
async def test_emits_fast_tweet_event(tmp_path: Path):
    db = Database(tmp_path / "x.db")
    await db.init()
    await db.upsert_x_user(handle="samczsun", user_id="111", tier="S")

    received_subs: list[dict] = []
    fast_tweet = json.loads(Path("tests/fixtures/x_tweet_ws_fast_tweet.json").read_text())
    fast_tweet["tweet"]["user"]["id_str"] = "111"

    async def handler(ws):
        sub_msg = await ws.recv()
        received_subs.append(json.loads(sub_msg))
        await ws.send(json.dumps(fast_tweet))
        await asyncio.sleep(0.05)
        await ws.close()

    server = await _start_mock_ws(handler)
    host, port = server.sockets[0].getsockname()[:2]
    url = f"ws://{host}:{port}"

    src = XWebSocketSource(
        api_key="k", websocket_url=url,
        ws_reconnect_min_seconds=1, ws_max_consecutive_failures=3,
        db=db,
    )
    q: asyncio.Queue = asyncio.Queue()

    task = asyncio.create_task(src.run(q))
    await asyncio.sleep(0.5)
    src.request_stop()
    await asyncio.wait_for(task, timeout=3)
    server.close()
    await server.wait_closed()

    assert received_subs, "client did not send subscription"
    sub_str = json.dumps(received_subs[0])
    assert "111" in sub_str

    got = []
    while not q.empty():
        got.append(await q.get())
    assert len(got) == 1
    assert got[0].source_kind == "x"
    assert got[0].raw["_tier"] == "S"
    assert got[0].raw["id_str"] == "1789012345678901234"

    rows = {u["user_id"]: u for u in await db.list_x_users()}
    assert rows["111"]["last_seen_id"] == "1789012345678901234"
    await db.close()


@pytest.mark.asyncio
async def test_marks_degraded_after_n_consecutive_failures(tmp_path: Path):
    db = Database(tmp_path / "x.db")
    await db.init()

    src = XWebSocketSource(
        api_key="k",
        websocket_url="ws://127.0.0.1:1",  # nothing listens
        ws_reconnect_min_seconds=0,
        ws_max_consecutive_failures=3,
        db=db,
    )
    q: asyncio.Queue = asyncio.Queue()

    task = asyncio.create_task(src.run(q))
    await asyncio.sleep(0.5)
    src.request_stop()
    await asyncio.wait_for(task, timeout=3)

    assert await db.is_degraded(src.name) is True
    await db.close()


@pytest.mark.asyncio
async def test_short_connection_counts_as_failure(tmp_path: Path):
    db = Database(tmp_path / "x.db")
    await db.init()
    await db.upsert_x_user(handle="samczsun", user_id="111", tier="S")

    async def handler(ws):
        # Receive subscription, then close immediately (simulates server reject)
        try:
            await ws.recv()
        except Exception:
            pass
        await ws.close()

    server = await websockets.serve(handler, "127.0.0.1", 0)
    host, port = server.sockets[0].getsockname()[:2]
    url = f"ws://{host}:{port}"

    src = XWebSocketSource(
        api_key="k", websocket_url=url,
        ws_reconnect_min_seconds=0,
        ws_max_consecutive_failures=2,
        db=db,
    )
    q: asyncio.Queue = asyncio.Queue()

    task = asyncio.create_task(src.run(q))
    await asyncio.sleep(0.6)  # Allow >=2 short-connection cycles
    src.request_stop()
    await asyncio.wait_for(task, timeout=3)
    server.close()
    await server.wait_closed()

    assert await db.is_degraded(src.name) is True
    await db.close()
