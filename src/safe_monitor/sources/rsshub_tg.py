from __future__ import annotations

import asyncio
import html
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx
import structlog

from safe_monitor.core.models import RawEvent
from safe_monitor.sources.base import Source
from safe_monitor.storage.db import Database

log = structlog.get_logger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    return html.unescape(_TAG_RE.sub("", s or "")).strip()


def _parse_pubdate(s: str | None) -> datetime | None:
    """Parse an RSS <pubDate> field (RFC 2822 format)."""
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except (TypeError, ValueError):
        return None


def _parse_checkpoint(s: str | None) -> datetime | None:
    """Parse our own stored checkpoint (ISO 8601)."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except (TypeError, ValueError):
        return None


class RSSHubTGPoller(Source):
    """Poll one RSSHub-wrapped Telegram channel as an RSS feed."""

    def __init__(
        self,
        *,
        name: str,
        username: str,
        rsshub_base_url: str,
        poll_interval_seconds: int,
        db: Database,
    ):
        self.name = name
        self._username = username
        self._base = rsshub_base_url.rstrip("/")
        self._interval = poll_interval_seconds
        self._db = db

    @property
    def _url(self) -> str:
        return f"{self._base}/telegram/channel/{self._username}"

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
            log.warning("rsshub.parse_error", source=self.name, error=str(e))
            return

        new_max = last_pubdate
        emitted = 0
        # Iterate <item> elements, namespace-agnostic
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
                source_kind="tg",
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
            await self._db.set_checkpoint(self.name, kind="tg_rss", cursor=new_max.isoformat())
        log.info("rsshub.poll_done", source=self.name, emitted=emitted)

    async def run(self, sink: asyncio.Queue[RawEvent]) -> None:
        while True:
            try:
                await self.poll_once(sink)
            except Exception as e:
                log.warning("rsshub.poll_error", source=self.name, error=str(e))
            await asyncio.sleep(self._interval)
