from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

import httpx
import structlog

from safe_monitor.core.models import RawEvent
from safe_monitor.sources.base import Source
from safe_monitor.sources.rsshub_tg import _parse_checkpoint, _parse_pubdate, _strip_html
from safe_monitor.storage.db import Database

log = structlog.get_logger(__name__)


class RssFeedPoller(Source):
    """Generic RSS 2.0 / Atom poller. Used for Rekt.news and any other
    standalone feed (i.e. not RSSHub-wrapped Telegram channels).

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
        for item in root.iter():
            if not item.tag.endswith("item"):
                continue
            guid = ""
            title = ""
            description = ""
            link: str | None = None
            pub: datetime | None = None
            for child in item:
                tag = child.tag.split("}", 1)[-1]
                text = (child.text or "").strip() if child.text else ""
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
