import asyncio
from pathlib import Path

import pytest
import respx
from httpx import Response

from safe_monitor.sources.rss_feed import RssFeedPoller
from safe_monitor.storage.db import Database


@pytest.mark.asyncio
async def test_rss_first_run_emits_all_items(tmp_path: Path):
    db = Database(tmp_path / "r.db")
    await db.init()
    body = Path("tests/fixtures/rekt_news_feed.xml").read_text()

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://rekt.news/rss/feed.xml").mock(
            return_value=Response(200, text=body)
        )
        poller = RssFeedPoller(
            name="rekt_news",
            endpoint="https://rekt.news/rss/feed.xml",
            poll_interval_seconds=3600,
            db=db,
        )
        q: asyncio.Queue = asyncio.Queue()
        await poller.poll_once(q)

    got = []
    while not q.empty():
        got.append(await q.get())

    assert len(got) == 2
    titles = {e.raw["title"] for e in got}
    assert "Example Bridge - Rekt" in titles
    assert all(e.source_kind == "api" for e in got)
    assert all(e.url and e.url.startswith("https://rekt.news/") for e in got)
    await db.close()


@pytest.mark.asyncio
async def test_rss_second_poll_skips_seen_items(tmp_path: Path):
    db = Database(tmp_path / "r.db")
    await db.init()
    body = Path("tests/fixtures/rekt_news_feed.xml").read_text()

    poller = RssFeedPoller(
        name="rekt_news",
        endpoint="https://rekt.news/feed",
        poll_interval_seconds=3600,
        db=db,
    )
    q: asyncio.Queue = asyncio.Queue()

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://rekt.news/feed").mock(return_value=Response(200, text=body))
        await poller.poll_once(q)
    while not q.empty():
        await q.get()

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://rekt.news/feed").mock(return_value=Response(200, text=body))
        await poller.poll_once(q)
    assert q.empty()
    await db.close()


@pytest.mark.asyncio
async def test_rss_handles_malformed_feed(tmp_path: Path):
    db = Database(tmp_path / "r.db")
    await db.init()

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://rekt.news/feed").mock(
            return_value=Response(200, text="<rss>broken")
        )
        poller = RssFeedPoller(
            name="rekt_news",
            endpoint="https://rekt.news/feed",
            poll_interval_seconds=3600,
            db=db,
        )
        q: asyncio.Queue = asyncio.Queue()
        await poller.poll_once(q)

    assert q.empty()
    await db.close()


@pytest.mark.asyncio
async def test_rss_parses_atom_feed(tmp_path: Path):
    db = Database(tmp_path / "r.db")
    await db.init()
    body = Path("tests/fixtures/wublock_feed.xml").read_text()

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://wublock123.com/feed").mock(
            return_value=Response(200, text=body)
        )
        poller = RssFeedPoller(
            name="wublock_news",
            endpoint="https://wublock123.com/feed",
            poll_interval_seconds=300,
            db=db,
        )
        q: asyncio.Queue = asyncio.Queue()
        await poller.poll_once(q)

    got = []
    while not q.empty():
        got.append(await q.get())

    assert len(got) == 2
    titles = {e.raw["title"] for e in got}
    assert "某 DeFi 协议遭攻击被盗约 1200 万美元" in titles
    # <link href="..."> attribute must be picked up, not the empty element text
    assert all(e.url and e.url.startswith("https://www.wublock123.com/news/") for e in got)
    # <id> becomes external_id
    assert all(e.external_id.startswith("https://www.wublock123.com/news/") for e in got)
    # body text stripped of <p> tags
    sec = next(e for e in got if "遭攻击" in e.raw["title"])
    assert "<p" not in sec.text
    assert "协议漏洞" in sec.text
    assert all(e.source_kind == "api" for e in got)
    await db.close()


@pytest.mark.asyncio
async def test_rss_atom_second_poll_skips_seen_items(tmp_path: Path):
    db = Database(tmp_path / "r.db")
    await db.init()
    body = Path("tests/fixtures/wublock_feed.xml").read_text()

    poller = RssFeedPoller(
        name="wublock_news",
        endpoint="https://wublock123.com/feed",
        poll_interval_seconds=300,
        db=db,
    )
    q: asyncio.Queue = asyncio.Queue()

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://wublock123.com/feed").mock(return_value=Response(200, text=body))
        await poller.poll_once(q)
    while not q.empty():
        await q.get()

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://wublock123.com/feed").mock(return_value=Response(200, text=body))
        await poller.poll_once(q)
    assert q.empty()
    await db.close()
