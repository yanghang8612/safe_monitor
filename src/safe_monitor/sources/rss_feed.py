from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from datetime import UTC, datetime

import httpx
import structlog

from safe_monitor.core.models import RawEvent
from safe_monitor.sources.base import Source
from safe_monitor.sources.rsshub_tg import _parse_checkpoint, _parse_pubdate, _strip_html
from safe_monitor.storage.db import Database

log = structlog.get_logger(__name__)

# Source names routed through this generic poller. main.py reads this to
# decide which `api.name` entries should be wired up to RssFeedPoller.
# Normalizer and filter keep their own narrower sets (incidents-only
# vs. newsflash mix) — see core/normalizer.py and core/filter.py.
RSS_FEED_SOURCES = frozenset({
    "rekt_news",
    "wublock_news",
    "jinse_news",
    "techflow_news",
    "foresight_news",
    "panews_news",
})


def _local_tag(tag: str) -> str:
    """Strip an XML namespace prefix: '{ns}item' -> 'item'."""
    return tag.split("}", 1)[-1]


def _is_atom(root: ET.Element) -> bool:
    """True for an Atom <feed> root, False for an RSS 2.0 <rss> root."""
    return _local_tag(root.tag) == "feed"


def _iter_entries(
    root: ET.Element,
) -> Iterator[tuple[str, str, str, str | None, datetime | None]]:
    """Yield (guid, title, description, link, pub) for each feed entry.

    Handles RSS 2.0 (<item>/<pubDate>) and Atom (<entry>/<published>)
    uniformly. Only the direct children of each entry are read, so
    feed-level <id>/<link>/<updated> elements never leak into an entry.
    """
    atom = _is_atom(root)
    entry_tag = "entry" if atom else "item"
    for node in root.iter():
        if not node.tag.endswith(entry_tag):
            continue
        guid = ""
        title = ""
        description = ""
        link: str | None = None
        pub: datetime | None = None
        for child in node:
            tag = _local_tag(child.tag)
            text = (child.text or "").strip() if child.text else ""
            if atom:
                if tag == "id":
                    guid = text
                elif tag == "title":
                    title = text
                elif tag == "content":
                    if text:
                        description = text
                elif tag == "summary":
                    if text and not description:
                        description = text
                elif tag == "link":
                    href = child.get("href")
                    if href and link is None:
                        link = href
                elif tag == "published":
                    pub = _parse_checkpoint(text)
                elif tag == "updated":
                    pub = pub or _parse_checkpoint(text)
            else:
                if tag == "guid":
                    guid = text
                elif tag == "title":
                    title = text
                elif tag == "description":
                    description = text
                elif tag == "link":
                    link = text or None
                elif tag == "pubDate":
                    pub = _parse_pubdate(text)
        yield guid, title, description, link, pub


class RssFeedPoller(Source):
    """Generic RSS 2.0 / Atom poller. Used for Rekt.news, wublock123, and any
    other standalone feed (i.e. not RSSHub-wrapped Telegram channels).

    Cursor: ISO-8601 string of the most recently seen item's pubDate.
    Emits RawEvent with source_kind='api'.
    """

    def __init__(
        self,
        *,
        name: str,
        endpoint: str,
        poll_interval_seconds: int,
        db: Database,
    ):
        self.name = name
        self._url = endpoint
        self._interval = poll_interval_seconds
        self._db = db

    async def poll_once(self, sink: asyncio.Queue[RawEvent]) -> None:
        cp = await self._db.get_checkpoint(self.name)
        last_cursor = cp["cursor"] if cp and cp.get("cursor") else ""
        last_pubdate = _parse_checkpoint(last_cursor) or datetime.fromtimestamp(0, tz=UTC)

        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.get(self._url)
            r.raise_for_status()
            feed_text = r.text

        try:
            root = ET.fromstring(feed_text)
        except ET.ParseError as e:
            log.warning("rss.parse_error", source=self.name, error=str(e))
            return

        new_max = last_pubdate
        emitted = 0
        for guid, title, description, link, pub in _iter_entries(root):
            if pub is not None and pub <= last_pubdate:
                continue

            clean = _strip_html(description) or title
            if not clean:
                continue

            ev = RawEvent(
                source=self.name,
                source_kind="api",
                external_id=guid or link or clean[:64],
                received_at=datetime.now(UTC),
                raw={"title": title, "description": description, "link": link, "guid": guid},
                text=clean,
                url=link,
                occurred_at=pub,
            )
            await sink.put(ev)
            if pub is not None and pub > new_max:
                new_max = pub
            emitted += 1

        if new_max > last_pubdate:
            await self._db.set_checkpoint(self.name, kind="rss", cursor=new_max.isoformat())
        log.info("rss.poll_done", source=self.name, emitted=emitted)

    async def run(self, sink: asyncio.Queue[RawEvent]) -> None:
        while True:
            try:
                await self.poll_once(sink)
            except Exception as e:
                log.warning("rss.poll_error", source=self.name, error=str(e))
            await asyncio.sleep(self._interval)
