import asyncio
from pathlib import Path

import pytest
import respx
from httpx import Response

from safe_monitor.sources.x_search_polling import XSearchPollingSource
from safe_monitor.storage.db import Database


def _make_source(db: Database) -> XSearchPollingSource:
    return XSearchPollingSource(
        api_key="k",
        rest_base_url="https://api.twitterapi.io",
        poll_interval_seconds=600,
        db=db,
        query_budget=500,
        max_pages_per_batch=3,
    )


@pytest.mark.asyncio
async def test_empty_search_advances_cursor_with_no_emission(tmp_path: Path):
    db = Database(tmp_path / "x.db")
    await db.init()
    await db.upsert_x_user(handle="samczsun", user_id="111", tier="S")
    await db.upsert_x_user(handle="zachxbt", user_id="222", tier="S")

    src = _make_source(db)
    q: asyncio.Queue = asyncio.Queue()

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.twitterapi.io/twitter/tweet/advanced_search").mock(
            return_value=Response(
                200,
                json={"tweets": [], "has_next_page": False, "next_cursor": ""},
            )
        )
        await src.poll_once(q)

    assert q.empty()
    rows = {u["user_id"]: u for u in await db.list_x_users()}
    # Both users should have last_seen_at_unix bumped (floor advance).
    assert rows["111"]["last_seen_at_unix"] is not None
    assert rows["222"]["last_seen_at_unix"] is not None
    assert rows["111"]["last_seen_id"] is None  # no tweet → id unchanged
    await db.close()


@pytest.mark.asyncio
async def test_returned_tweet_attributed_to_author_and_tier_tagged(tmp_path: Path):
    db = Database(tmp_path / "x.db")
    await db.init()
    await db.upsert_x_user(handle="samczsun", user_id="111", tier="S")
    await db.upsert_x_user(handle="zachxbt", user_id="222", tier="S")

    src = _make_source(db)
    q: asyncio.Queue = asyncio.Queue()

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.twitterapi.io/twitter/tweet/advanced_search").mock(
            return_value=Response(
                200,
                json={
                    "tweets": [
                        {
                            "id_str": "9001",
                            "text": "exploit incoming",
                            "createdAt": "2024-09-22T12:34:56Z",
                            "author": {"id_str": "111", "screen_name": "samczsun"},
                        }
                    ],
                    "has_next_page": False,
                    "next_cursor": "",
                },
            )
        )
        await src.poll_once(q)

    got = []
    while not q.empty():
        got.append(await q.get())
    assert len(got) == 1
    assert got[0].external_id == "9001"
    assert got[0].raw["_tier"] == "S"

    rows = {u["user_id"]: u for u in await db.list_x_users()}
    assert rows["111"]["last_seen_id"] == "9001"
    assert rows["111"]["last_seen_at_unix"] >= 1727008496
    # Other user in batch advanced too (no tweets, but cursor moved).
    assert rows["222"]["last_seen_id"] is None
    assert rows["222"]["last_seen_at_unix"] is not None
    await db.close()


@pytest.mark.asyncio
async def test_pagination_consumes_multiple_pages(tmp_path: Path):
    db = Database(tmp_path / "x.db")
    await db.init()
    await db.upsert_x_user(handle="samczsun", user_id="111", tier="S")

    src = _make_source(db)
    q: asyncio.Queue = asyncio.Queue()

    with respx.mock(assert_all_called=True) as mock:
        # Page 1: has_next_page=true → server expects cursor on page 2
        page1 = Response(
            200,
            json={
                "tweets": [
                    {
                        "id_str": "1001",
                        "text": "t1",
                        "createdAt": "2024-09-22T10:00:00Z",
                        "author": {"id_str": "111", "screen_name": "samczsun"},
                    }
                ],
                "has_next_page": True,
                "next_cursor": "cur-1",
            },
        )
        page2 = Response(
            200,
            json={
                "tweets": [
                    {
                        "id_str": "1002",
                        "text": "t2",
                        "createdAt": "2024-09-22T11:00:00Z",
                        "author": {"id_str": "111", "screen_name": "samczsun"},
                    }
                ],
                "has_next_page": False,
                "next_cursor": "",
            },
        )
        # respx returns by call order with side_effect when same route is matched
        mock.get("https://api.twitterapi.io/twitter/tweet/advanced_search").mock(
            side_effect=[page1, page2]
        )
        await src.poll_once(q)

    got = []
    while not q.empty():
        got.append(await q.get())
    assert {e.external_id for e in got} == {"1001", "1002"}
    rows = {u["user_id"]: u for u in await db.list_x_users()}
    assert rows["111"]["last_seen_id"] == "1002"
    await db.close()


@pytest.mark.asyncio
async def test_repoll_skips_already_seen_tweet(tmp_path: Path):
    db = Database(tmp_path / "x.db")
    await db.init()
    await db.upsert_x_user(handle="samczsun", user_id="111", tier="S")

    src = _make_source(db)
    q: asyncio.Queue = asyncio.Queue()

    tweet = {
        "id_str": "9001",
        "text": "first",
        "createdAt": "2024-09-22T12:34:56Z",
        "author": {"id_str": "111", "screen_name": "samczsun"},
    }
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.twitterapi.io/twitter/tweet/advanced_search").mock(
            return_value=Response(
                200,
                json={"tweets": [tweet], "has_next_page": False, "next_cursor": ""},
            )
        )
        await src.poll_once(q)
    assert not q.empty()
    while not q.empty():
        await q.get()

    # Re-poll: server returns same tweet (boundary case from since_time being
    # inclusive). Must not re-emit.
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.twitterapi.io/twitter/tweet/advanced_search").mock(
            return_value=Response(
                200,
                json={"tweets": [tweet], "has_next_page": False, "next_cursor": ""},
            )
        )
        await src.poll_once(q)
    assert q.empty()
    await db.close()


@pytest.mark.asyncio
async def test_402_marks_degraded(tmp_path: Path):
    db = Database(tmp_path / "x.db")
    await db.init()
    await db.upsert_x_user(handle="samczsun", user_id="111", tier="S")

    src = _make_source(db)
    q: asyncio.Queue = asyncio.Queue()
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.twitterapi.io/twitter/tweet/advanced_search").mock(
            return_value=Response(402, text="no credits")
        )
        await src.poll_once(q)
    assert await db.is_degraded("x_polling") is True
    await db.close()
