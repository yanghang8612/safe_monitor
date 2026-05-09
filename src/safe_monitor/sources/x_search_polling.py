"""Polls /twitter/tweet/advanced_search with batched OR queries.

Cost model: 15 credits per returned tweet, 15 credits floor when empty —
so an idle batch costs ~15 credits regardless of how many handles share
the OR query. This is ~25× cheaper than the per-handle last_tweets path.

State per handle (x_users):
- last_seen_at_unix: max created_at consumed; the next poll's since_time
  is min(this) across the batch.
- last_seen_id: max tweet id consumed; defensive dedup so a tweet that
  the search index returns twice (e.g. via boundary timing) is suppressed.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import structlog

from safe_monitor.core.models import RawEvent
from safe_monitor.sources.base import Source
from safe_monitor.sources.x_client import TwitterApiIoClient
from safe_monitor.storage.db import Database

log = structlog.get_logger(__name__)


def _pack_handles_into_query_batches(
    handles: list[str], budget: int = 500
) -> list[list[str]]:
    """Greedy-pack `from:<h>` tokens under `budget` chars per batch."""
    out: list[list[str]] = []
    cur: list[str] = []
    cur_len = 0
    for h in handles:
        token = f"from:{h}"
        added_len = len(token) if not cur else len(token) + 4  # ' OR '
        if cur and cur_len + added_len > budget:
            out.append(cur)
            cur, cur_len = [h], len(token)
        else:
            cur.append(h)
            cur_len += added_len
    if cur:
        out.append(cur)
    return out


def _parse_created_at(value: Any) -> int | None:
    """Best-effort parse of TwitterAPI.io `createdAt` into Unix seconds."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str):
        return None
    s = value.strip()
    # Modern ISO-8601: "2024-09-22T12:34:56.000Z"
    try:
        if s.endswith("Z"):
            return int(datetime.fromisoformat(s[:-1] + "+00:00").timestamp())
        return int(datetime.fromisoformat(s).timestamp())
    except ValueError:
        pass
    # Legacy Twitter: "Wed Oct 10 20:19:24 +0000 2018"
    try:
        return int(
            datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y").timestamp()
        )
    except ValueError:
        return None


class XSearchPollingSource(Source):
    """Batched OR-query polling via /twitter/tweet/advanced_search."""

    name = "x_polling"

    SAFETY_LAG_SECONDS = 60
    MAX_HISTORICAL_LOOKBACK = 24 * 3600

    def __init__(
        self,
        *,
        api_key: str,
        rest_base_url: str,
        poll_interval_seconds: int,
        db: Database,
        query_budget: int = 500,
        max_pages_per_batch: int = 5,
    ):
        self._base = rest_base_url
        self._interval = poll_interval_seconds
        self._db = db
        self._client = TwitterApiIoClient(api_key=api_key, base_url=rest_base_url)
        self._query_budget = query_budget
        self._max_pages = max_pages_per_batch

    def _resolve_author(
        self, tweet: dict[str, Any], by_user_id: dict[str, dict[str, Any]],
        by_handle_lower: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        author = tweet.get("author") or tweet.get("user") or {}
        if not isinstance(author, dict):
            return None
        uid = str(author.get("id") or author.get("id_str") or "")
        if uid and uid in by_user_id:
            return by_user_id[uid]
        screen = author.get("screen_name") or author.get("userName") or ""
        if screen:
            return by_handle_lower.get(str(screen).lower())
        return None

    def _compute_since_time(
        self, batch_users: list[dict[str, Any]], now_unix: int
    ) -> int:
        floor = now_unix - self.MAX_HISTORICAL_LOOKBACK
        default_for_new = now_unix - self._interval - self.SAFETY_LAG_SECONDS
        ts: list[int] = []
        for u in batch_users:
            v = u.get("last_seen_at_unix")
            ts.append(int(v) if v is not None else default_for_new)
        if not ts:
            return default_for_new
        return max(min(ts), floor)

    async def _poll_batch(
        self,
        sink: asyncio.Queue[RawEvent],
        batch_handles: list[str],
        users_by_user_id: dict[str, dict[str, Any]],
        users_by_handle_lower: dict[str, dict[str, Any]],
        now_unix: int,
    ) -> None:
        batch_users = [users_by_handle_lower[h.lower()] for h in batch_handles]
        since_time = self._compute_since_time(batch_users, now_unix)
        query = " OR ".join(f"from:{h}" for h in batch_handles)

        cursor: str | None = None
        per_user_max_id: dict[str, int] = {}
        per_user_max_at: dict[str, int] = {}
        consumed_any = False
        oldest_consumed_unix: int | None = None
        newest_consumed_unix: int | None = None
        truncated = False

        for page in range(self._max_pages):
            try:
                resp = await self._client.search_tweets(
                    query=query, since_time_unix=since_time, cursor=cursor
                )
            except TwitterApiIoClient.CreditsExhausted as e:
                await self._db.set_degraded(self.name, True, reason=f"402: {e}")
                log.warning("x_polling.degraded_credits", batch=batch_handles[:3])
                return
            except TwitterApiIoClient.TransientError as e:
                log.warning("x_polling.transient", error=str(e), page=page)
                return
            except Exception as e:
                log.warning("x_polling.error", error=str(e), page=page)
                return

            tweets = resp["tweets"]
            if not tweets:
                break

            tweets_sorted = sorted(
                tweets, key=lambda t: int(t.get("id_str") or t.get("id") or 0)
            )
            for t in tweets_sorted:
                u = self._resolve_author(t, users_by_user_id, users_by_handle_lower)
                if u is None:
                    continue
                tid_str = str(t.get("id_str") or t.get("id") or "")
                if not tid_str:
                    continue
                try:
                    tid = int(tid_str)
                except ValueError:
                    continue
                last_id = u.get("last_seen_id")
                if last_id and tid <= int(last_id):
                    continue
                created_unix = _parse_created_at(
                    t.get("createdAt") or t.get("created_at")
                )
                if created_unix is None:
                    created_unix = now_unix

                payload = dict(t)
                payload["_tier"] = u["tier"]
                ev = RawEvent(
                    source=self.name,
                    source_kind="x",
                    external_id=tid_str,
                    received_at=datetime.now(UTC),
                    raw=payload,
                    text=t.get("text") or t.get("full_text") or "",
                )
                await sink.put(ev)
                consumed_any = True

                uid = u["user_id"]
                if tid > per_user_max_id.get(uid, 0):
                    per_user_max_id[uid] = tid
                if created_unix > per_user_max_at.get(uid, 0):
                    per_user_max_at[uid] = created_unix
                if oldest_consumed_unix is None or created_unix < oldest_consumed_unix:
                    oldest_consumed_unix = created_unix
                if newest_consumed_unix is None or created_unix > newest_consumed_unix:
                    newest_consumed_unix = created_unix

            if not resp["has_next_page"] or not resp["next_cursor"]:
                break
            cursor = resp["next_cursor"]
        else:
            truncated = True

        if consumed_any:
            if truncated and oldest_consumed_unix is not None:
                batch_advance_to = oldest_consumed_unix - 1
            else:
                batch_advance_to = newest_consumed_unix or since_time
        else:
            batch_advance_to = now_unix - self.SAFETY_LAG_SECONDS

        for u in batch_users:
            uid = u["user_id"]
            if uid in per_user_max_id:
                await self._db.set_x_user_last_seen(uid, str(per_user_max_id[uid]))
            await self._db.set_x_user_last_seen_at(uid, batch_advance_to)

        log.info(
            "x_polling.batch_done",
            handles=len(batch_handles),
            since=since_time,
            advance_to=batch_advance_to,
            emitted=sum(1 for _ in per_user_max_id),
            truncated=truncated,
        )

    async def poll_once(self, sink: asyncio.Queue[RawEvent]) -> None:
        if await self._db.is_degraded(self.name):
            log.info("x_polling.skip_degraded")
            return
        users = await self._db.list_x_users()
        if not users:
            return

        users_by_user_id = {u["user_id"]: u for u in users}
        users_by_handle_lower = {u["handle"].lower(): u for u in users}
        handles = [u["handle"] for u in users]
        batches = _pack_handles_into_query_batches(handles, self._query_budget)
        now_unix = int(datetime.now(UTC).timestamp())

        for batch in batches:
            await self._poll_batch(
                sink, batch, users_by_user_id, users_by_handle_lower, now_unix
            )

    async def run(self, sink: asyncio.Queue[RawEvent]) -> None:
        while True:
            try:
                await self.poll_once(sink)
            except Exception as e:
                log.warning("x_polling.run_error", error=str(e))
            await asyncio.sleep(self._interval)
