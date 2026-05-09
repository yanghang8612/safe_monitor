from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import structlog
import websockets
from websockets.exceptions import ConnectionClosed

from safe_monitor.core.models import RawEvent
from safe_monitor.sources.base import Source
from safe_monitor.storage.db import Database

log = structlog.get_logger(__name__)


class XWebSocketSource(Source):
    name = "x_websocket"

    def __init__(
        self,
        *,
        api_key: str,
        websocket_url: str,
        ws_reconnect_min_seconds: int,
        ws_max_consecutive_failures: int,
        db: Database,
    ):
        self._api_key = api_key
        self._url = websocket_url
        self._reconnect_min = ws_reconnect_min_seconds
        self._max_fails = ws_max_consecutive_failures
        self._db = db
        self._stop = asyncio.Event()

    def request_stop(self) -> None:
        self._stop.set()

    def _build_subscription(self, user_ids: list[str]) -> dict[str, Any]:
        # NOTE: TwitterAPI.io documents `follow: [user_id, ...]` as the rule
        # vocabulary. Wire format below is best-effort; verify against a real
        # connection (Step 8.5 is deferred to the user) and update if rejected.
        return {"action": "subscribe", "rules": [{"follow": user_ids}]}

    async def _user_id_to_tier(self) -> dict[str, str]:
        rows = await self._db.list_x_users()
        return {r["user_id"]: r["tier"] for r in rows}

    async def _handle_payload(
        self, payload: dict[str, Any], tier_map: dict[str, str], sink: asyncio.Queue[RawEvent]
    ) -> None:
        et = payload.get("event_type")
        tweets: list[dict[str, Any]] = []
        if et == "fast_tweet":
            t = payload.get("tweet")
            if t:
                tweets.append(t)
        elif et == "tweet":
            tweets.extend(payload.get("tweets") or [])
        else:
            log.debug("x_ws.unknown_event_type", payload=payload)
            return

        for t in tweets:
            user = t.get("author") or t.get("user") or {}
            uid = str(user.get("id_str") or user.get("id") or "")
            tier = tier_map.get(uid, "E")
            tid = str(t.get("id_str") or t.get("id") or "")
            if not tid:
                continue
            tagged = dict(t)
            tagged["_tier"] = tier
            ev = RawEvent(
                source=self.name,
                source_kind="x",
                external_id=tid,
                received_at=datetime.now(UTC),
                raw=tagged,
                text=t.get("text") or t.get("full_text") or "",
            )
            await sink.put(ev)
            await self._db.set_x_user_last_seen(uid, tid)

    async def _interruptible_sleep(self, seconds: float) -> None:
        """Sleep that exits early when stop is requested."""
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def _one_connection(self, sink: asyncio.Queue[RawEvent]) -> None:
        tier_map = await self._user_id_to_tier()
        user_ids = list(tier_map.keys())
        if not user_ids:
            log.warning("x_ws.no_users_resolved")
            raise RuntimeError("no tracked x_users — cannot subscribe")

        # websockets 13.x uses extra_headers (renamed from additional_headers in v12)
        async with websockets.connect(
            self._url, extra_headers={"x-api-key": self._api_key}
        ) as ws:
            await ws.send(json.dumps(self._build_subscription(user_ids)))
            log.info("x_ws.connected", users=len(user_ids))

            stop_task = asyncio.create_task(self._stop.wait())
            try:
                while not self._stop.is_set():
                    recv_task = asyncio.create_task(ws.recv())
                    done, pending = await asyncio.wait(
                        {recv_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if stop_task in done:
                        recv_task.cancel()
                        return
                    try:
                        raw = recv_task.result()
                    except ConnectionClosed:
                        return
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        log.warning("x_ws.bad_json", raw=raw[:200])
                        continue
                    await self._handle_payload(payload, tier_map, sink)
            finally:
                stop_task.cancel()

    async def run(self, sink: asyncio.Queue[RawEvent]) -> None:
        consecutive = 0
        SHORT_CONNECTION_SECS = 30
        while not self._stop.is_set():
            if await self._db.is_degraded(self.name):
                log.info("x_ws.skip_degraded")
                await self._interruptible_sleep(min(60, max(self._reconnect_min, 5)))
                continue
            connect_started = asyncio.get_event_loop().time()
            try:
                await self._one_connection(sink)
                elapsed = asyncio.get_event_loop().time() - connect_started
                if elapsed < SHORT_CONNECTION_SECS and not self._stop.is_set():
                    consecutive += 1
                    log.warning("x_ws.short_connection", elapsed=elapsed, attempt=consecutive)
                    if consecutive >= self._max_fails:
                        await self._db.set_degraded(
                            self.name, True,
                            reason=f"{consecutive} short connections (<{SHORT_CONNECTION_SECS}s)",
                        )
                        log.error("x_ws.degraded_short_conn", consecutive=consecutive)
                        consecutive = 0
                else:
                    consecutive = 0
                if not self._stop.is_set():
                    await self._interruptible_sleep(self._reconnect_min)
            except Exception as e:
                consecutive += 1
                log.warning("x_ws.failure", attempt=consecutive, error=str(e))
                if consecutive >= self._max_fails:
                    await self._db.set_degraded(
                        self.name, True, reason=f"{consecutive} consecutive failures: {e}"
                    )
                    log.error("x_ws.degraded", consecutive=consecutive)
                    consecutive = 0
                await self._interruptible_sleep(self._reconnect_min)
