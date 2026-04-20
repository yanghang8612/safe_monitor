from __future__ import annotations

import asyncio
import json
from typing import Sequence

import structlog

from safe_monitor.core.deduper import Deduper
from safe_monitor.core.filter import Filter
from safe_monitor.core.models import RawEvent
from safe_monitor.core.normalizer import Normalizer
from safe_monitor.publishers.base import Publisher
from safe_monitor.sources.base import Source
from safe_monitor.storage.db import Database

log = structlog.get_logger(__name__)


class Orchestrator:
    def __init__(
        self,
        *,
        sources: Sequence[Source],
        publisher: Publisher,
        db: Database,
        filter_: Filter,
    ):
        self._sources = list(sources)
        self._publisher = publisher
        self._db = db
        self._filter = filter_
        self._normalizer = Normalizer()
        self._deduper = Deduper(db)
        self._queue: asyncio.Queue[RawEvent] = asyncio.Queue(maxsize=1000)
        self._shutdown = asyncio.Event()

    def request_shutdown(self) -> None:
        self._shutdown.set()

    async def _supervise(self, source: Source) -> None:
        backoff = 1
        while not self._shutdown.is_set():
            try:
                await source.run(self._queue)
                # normal exit
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.error("source.crashed", source=source.name, error=str(e))
                await asyncio.sleep(min(backoff, 300))
                backoff = min(backoff * 2, 300)

    async def _worker(self) -> None:
        while not self._shutdown.is_set():
            try:
                raw = await asyncio.wait_for(self._queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            await self._handle(raw)

    async def _handle(self, raw: RawEvent) -> None:
        try:
            event = self._normalizer.normalize(raw)
            if event is None:
                return
            if await self._deduper.is_duplicate(event):
                log.info("event.dedup", fp=event.fingerprint, source=raw.source)
                return
            if not self._filter.allow(event):
                await self._db.log_event(
                    source=event.source,
                    received_at=event.received_at.isoformat(),
                    published_at=None,
                    severity=event.severity.name,
                    title=event.title,
                    url=event.url,
                    raw_json=json.dumps(event.raw, default=str),
                    filter_decision="filtered",
                )
                return
            ok = await self._publisher.publish(event)
            from datetime import datetime, timezone
            await self._db.log_event(
                source=event.source,
                received_at=event.received_at.isoformat(),
                published_at=datetime.now(timezone.utc).isoformat() if ok else None,
                severity=event.severity.name,
                title=event.title,
                url=event.url,
                raw_json=json.dumps(event.raw, default=str),
                filter_decision="published" if ok else "publish_failed",
            )
        except Exception as e:
            log.error("handle.error", error=str(e), source=raw.source)

    async def run(self) -> None:
        source_tasks = [asyncio.create_task(self._supervise(s)) for s in self._sources]
        worker_task = asyncio.create_task(self._worker())
        try:
            await self._shutdown.wait()
        finally:
            for t in source_tasks:
                t.cancel()
            worker_task.cancel()
            await asyncio.gather(*source_tasks, worker_task, return_exceptions=True)
