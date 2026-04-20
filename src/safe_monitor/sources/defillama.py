from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from safe_monitor.core.models import RawEvent
from safe_monitor.sources.base import Source
from safe_monitor.storage.db import Database

log = structlog.get_logger(__name__)


class DefiLlamaHacksPoller(Source):
    def __init__(
        self,
        *,
        name: str,
        endpoint: str,
        poll_interval_seconds: int,
        db: Database,
    ):
        self.name = name
        self._endpoint = endpoint
        self._interval = poll_interval_seconds
        self._db = db

    async def poll_once(self, sink: asyncio.Queue[RawEvent]) -> None:
        cp = await self._db.get_checkpoint(self.name)
        if cp and cp.get("cursor"):
            last_ts = int(cp["cursor"])
        else:
            # First run: don't flood the channel with the full historical
            # hacks dump. Seed the checkpoint to "now" and exit; the next
            # tick will emit only genuinely new entries.
            last_ts = int(datetime.now(UTC).timestamp())
            await self._db.set_checkpoint(self.name, kind="api_poll", cursor=str(last_ts))
            log.info("defillama.first_run_seeded", last_ts=last_ts)
            return

        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(self._endpoint)
            r.raise_for_status()
            items: list[dict[str, Any]] = r.json()

        new_max = last_ts
        emitted = 0
        for item in sorted(items, key=lambda i: int(i.get("date", 0))):
            ts = int(item.get("date", 0))
            if ts <= last_ts:
                continue
            ev = RawEvent(
                source=self.name,
                source_kind="api",
                external_id=f"{item.get('name', '?')}|{ts}",
                received_at=datetime.now(UTC),
                raw=item,
                occurred_at=datetime.fromtimestamp(ts, tz=UTC),
                url=item.get("link"),
            )
            await sink.put(ev)
            new_max = max(new_max, ts)
            emitted += 1

        if new_max > last_ts:
            await self._db.set_checkpoint(self.name, kind="api_poll", cursor=str(new_max))
        log.info("defillama.poll_done", emitted=emitted, last_ts=new_max)

    async def run(self, sink: asyncio.Queue[RawEvent]) -> None:
        while True:
            try:
                await self.poll_once(sink)
            except Exception as e:
                log.warning("defillama.poll_error", error=str(e))
            await asyncio.sleep(self._interval)
