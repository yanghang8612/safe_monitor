from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import structlog

from safe_monitor.core.models import RawEvent
from safe_monitor.sources.base import Source
from safe_monitor.sources.x_client import TwitterApiIoClient
from safe_monitor.storage.db import Database

log = structlog.get_logger(__name__)


class XPollingSource(Source):
    """Per-user backfill poller. Fills the gap during WS reconnects."""

    name = "x_polling"

    def __init__(
        self,
        *,
        api_key: str,
        rest_base_url: str,
        poll_interval_seconds: int,
        db: Database,
    ):
        self._api_key = api_key
        self._base = rest_base_url
        self._interval = poll_interval_seconds
        self._db = db
        self._client = TwitterApiIoClient(api_key=api_key, base_url=rest_base_url)

    async def poll_once(self, sink: asyncio.Queue[RawEvent]) -> None:
        if await self._db.is_degraded(self.name):
            log.info("x_polling.skip_degraded")
            return
        users = await self._db.list_x_users()
        for u in users:
            try:
                tweets = await self._client.get_last_tweets(
                    user_id=u["user_id"], since_id=u["last_seen_id"]
                )
            except TwitterApiIoClient.CreditsExhausted as e:
                await self._db.set_degraded(self.name, True, reason=f"402: {e}")
                log.warning("x_polling.degraded_credits", user=u["handle"])
                return
            except TwitterApiIoClient.TransientError as e:
                log.warning("x_polling.transient", user=u["handle"], error=str(e))
                continue
            except Exception as e:
                log.warning("x_polling.error", user=u["handle"], error=str(e))
                continue
            if not tweets:
                continue
            # tweets are newest-first; emit oldest-first so cursor moves monotonically
            tweets_sorted = sorted(tweets, key=lambda t: int(t.get("id_str") or t.get("id") or 0))
            for t in tweets_sorted:
                tid = str(t.get("id_str") or t.get("id"))
                payload = dict(t)
                payload["_tier"] = u["tier"]
                ev = RawEvent(
                    source=self.name,
                    source_kind="x",
                    external_id=tid,
                    received_at=datetime.now(UTC),
                    raw=payload,
                    text=t.get("text") or t.get("full_text") or "",
                )
                await sink.put(ev)
            newest_id = str(tweets_sorted[-1].get("id_str") or tweets_sorted[-1].get("id"))
            await self._db.set_x_user_last_seen(u["user_id"], newest_id)

    async def run(self, sink: asyncio.Queue[RawEvent]) -> None:
        while True:
            try:
                await self.poll_once(sink)
            except Exception as e:
                log.warning("x_polling.run_error", error=str(e))
            await asyncio.sleep(self._interval)
