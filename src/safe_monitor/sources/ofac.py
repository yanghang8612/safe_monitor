from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import httpx
import structlog

from safe_monitor.core.models import RawEvent
from safe_monitor.sources.base import Source
from safe_monitor.storage.db import Database

log = structlog.get_logger(__name__)


class OfacSdnPoller(Source):
    def __init__(self, *, name: str, endpoint: str, poll_interval_seconds: int, db: Database):
        self.name = name
        self._endpoint = endpoint
        self._interval = poll_interval_seconds
        self._db = db

    def _extract_crypto_addrs(self, xml_text: str) -> list[dict]:
        root = ET.fromstring(xml_text)
        items: list[dict] = []
        # Iterate namespaces-agnostically
        for entry in root.iter():
            if not entry.tag.endswith("sdnEntry"):
                continue
            uid = ""
            name = ""
            for child in entry:
                tag = child.tag.split("}", 1)[-1]
                if tag == "uid":
                    uid = (child.text or "").strip()
                if tag == "firstName" and not name:
                    name = (child.text or "").strip()
            for id_el in entry.iter():
                if not id_el.tag.endswith("id"):
                    continue
                id_type = ""
                id_number = ""
                for c in id_el:
                    t = c.tag.split("}", 1)[-1]
                    if t == "idType":
                        id_type = (c.text or "").strip()
                    if t == "idNumber":
                        id_number = (c.text or "").strip()
                if "Digital Currency" in id_type and id_number:
                    items.append({
                        "uid": uid,
                        "name": name,
                        "id_type": id_type,
                        "address": id_number,
                    })
        return items

    async def poll_once(self, sink: asyncio.Queue[RawEvent]) -> None:
        cp = await self._db.get_checkpoint(self.name)
        seen_keys = set((cp.get("cursor") or "").split(",")) if cp else set()
        seen_keys.discard("")

        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.get(self._endpoint)
            r.raise_for_status()
            xml_text = r.text

        entries = self._extract_crypto_addrs(xml_text)
        new_keys = []
        emitted = 0
        for e in entries:
            key = f"{e['uid']}:{e['address']}"
            if key in seen_keys:
                continue
            text = f"OFAC sanctioned: {e['name']} ({e['id_type']}) {e['address']}"
            ev = RawEvent(
                source=self.name,
                source_kind="api",
                external_id=key,
                received_at=datetime.now(timezone.utc),
                raw=e,
                text=text,
            )
            await sink.put(ev)
            new_keys.append(key)
            emitted += 1

        if new_keys:
            all_keys = sorted(seen_keys | set(new_keys))
            await self._db.set_checkpoint(self.name, kind="api_poll", cursor=",".join(all_keys))
        log.info("ofac.poll_done", emitted=emitted)

    async def run(self, sink: asyncio.Queue[RawEvent]) -> None:
        while True:
            try:
                await self.poll_once(sink)
            except Exception as e:
                log.warning("ofac.poll_error", error=str(e))
            await asyncio.sleep(self._interval)
