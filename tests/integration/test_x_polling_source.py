import asyncio
from pathlib import Path

import pytest
import respx
from httpx import Response

from safe_monitor.sources.x_polling import XPollingSource
from safe_monitor.storage.db import Database


@pytest.mark.asyncio
async def test_polls_each_user_emits_only_new_tweets(tmp_path: Path):
    db = Database(tmp_path / "x.db")
    await db.init()
    await db.upsert_x_user(handle="samczsun", user_id="111", tier="S")
    await db.upsert_x_user(handle="WuBlockchain", user_id="222", tier="D")

    src = XPollingSource(
        api_key="k",
        rest_base_url="https://api.twitterapi.io",
        poll_interval_seconds=1,
        db=db,
    )
    q: asyncio.Queue = asyncio.Queue()

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.twitterapi.io/twitter/user/last_tweets",
                 params={"userId": "111", "limit": 20}).mock(
            return_value=Response(200, json={"tweets": [
                {"id_str": "1001", "text": "samczsun first", "user": {"id_str": "111", "screen_name": "samczsun"}},
            ]})
        )
        mock.get("https://api.twitterapi.io/twitter/user/last_tweets",
                 params={"userId": "222", "limit": 20}).mock(
            return_value=Response(200, json={"tweets": [
                {"id_str": "2001", "text": "wu first", "user": {"id_str": "222", "screen_name": "WuBlockchain"}},
            ]})
        )
        await src.poll_once(q)

    got = []
    while not q.empty():
        got.append(await q.get())
    assert {e.external_id for e in got} == {"1001", "2001"}
    assert {e.raw["_tier"] for e in got} == {"S", "D"}

    rows = {u["user_id"]: u for u in await db.list_x_users()}
    assert rows["111"]["last_seen_id"] == "1001"
    assert rows["222"]["last_seen_id"] == "2001"

    # Re-poll with same response → no new emissions
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.twitterapi.io/twitter/user/last_tweets",
                 params={"userId": "111", "limit": 20}).mock(
            return_value=Response(200, json={"tweets": [
                {"id_str": "1001", "text": "same", "user": {"id_str": "111", "screen_name": "samczsun"}},
            ]})
        )
        mock.get("https://api.twitterapi.io/twitter/user/last_tweets",
                 params={"userId": "222", "limit": 20}).mock(
            return_value=Response(200, json={"tweets": [
                {"id_str": "2001", "text": "same", "user": {"id_str": "222", "screen_name": "WuBlockchain"}},
            ]})
        )
        await src.poll_once(q)
    assert q.empty()
    await db.close()


@pytest.mark.asyncio
async def test_skips_polling_when_degraded(tmp_path: Path):
    db = Database(tmp_path / "x.db")
    await db.init()
    await db.upsert_x_user(handle="samczsun", user_id="111", tier="S")
    await db.set_degraded("x_polling", True, reason="manual")

    src = XPollingSource(
        api_key="k",
        rest_base_url="https://api.twitterapi.io",
        poll_interval_seconds=1,
        db=db,
    )
    q: asyncio.Queue = asyncio.Queue()
    # No respx mock — if it tried to hit the network the test would fail.
    await src.poll_once(q)
    assert q.empty()
    await db.close()


@pytest.mark.asyncio
async def test_402_marks_degraded(tmp_path: Path):
    db = Database(tmp_path / "x.db")
    await db.init()
    await db.upsert_x_user(handle="samczsun", user_id="111", tier="S")

    src = XPollingSource(
        api_key="k",
        rest_base_url="https://api.twitterapi.io",
        poll_interval_seconds=1,
        db=db,
    )
    q: asyncio.Queue = asyncio.Queue()
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.twitterapi.io/twitter/user/last_tweets",
                 params={"userId": "111", "limit": 20}).mock(
            return_value=Response(402, text="no credits")
        )
        await src.poll_once(q)
    assert await db.is_degraded("x_polling") is True
    await db.close()
