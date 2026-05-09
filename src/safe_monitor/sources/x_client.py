from __future__ import annotations

from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)


class TwitterApiIoClient:
    """Thin async client over TwitterAPI.io REST endpoints.

    Header name is 'x-api-key' (lowercase or X-API-Key — server accepts both).
    All methods are idempotent and safe to retry.
    """

    class CreditsExhausted(Exception):
        pass

    class TransientError(Exception):
        pass

    def __init__(self, *, api_key: str, base_url: str, timeout: float = 20.0):
        self._api_key = api_key
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self._api_key, "accept": "application/json"}

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base}{path}"
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.get(url, headers=self._headers(), params=params)
        if r.status_code == 402:
            raise self.CreditsExhausted(r.text[:300])
        if r.status_code == 429:
            raise self.TransientError(f"429 rate limited: {r.text[:200]}")
        if r.status_code == 404:
            return {}
        if r.status_code >= 500:
            raise self.TransientError(f"{r.status_code}: {r.text[:200]}")
        r.raise_for_status()
        return r.json()

    async def _request_json(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        url = f"{self._base}{path}"
        headers = {**self._headers(), "content-type": "application/json"}
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.request(method, url, headers=headers, json=body)
        if r.status_code == 402:
            raise self.CreditsExhausted(r.text[:300])
        if r.status_code == 429:
            raise self.TransientError(f"429 rate limited: {r.text[:200]}")
        if r.status_code >= 500:
            raise self.TransientError(f"{r.status_code}: {r.text[:200]}")
        r.raise_for_status()
        return r.json()

    async def list_filter_rules(self) -> list[dict[str, Any]]:
        data = await self._get("/oapi/tweet_filter/get_rules", {})
        rules = data.get("rules")
        return list(rules) if isinstance(rules, list) else []

    async def add_filter_rule(
        self, *, tag: str, value: str, interval_seconds: int = 60
    ) -> str:
        resp = await self._request_json(
            "POST",
            "/oapi/tweet_filter/add_rule",
            {"tag": tag, "value": value, "interval_seconds": interval_seconds},
        )
        rid = resp.get("rule_id") or (resp.get("data") or {}).get("rule_id")
        if not rid:
            raise self.TransientError(f"add_rule: missing rule_id in response: {resp}")
        return str(rid)

    async def update_filter_rule(
        self,
        *,
        rule_id: str,
        tag: str,
        value: str,
        interval_seconds: int,
        is_effect: int,
    ) -> None:
        await self._request_json(
            "POST",
            "/oapi/tweet_filter/update_rule",
            {
                "rule_id": rule_id,
                "tag": tag,
                "value": value,
                "interval_seconds": interval_seconds,
                "is_effect": is_effect,
            },
        )

    async def delete_filter_rule(self, rule_id: str) -> None:
        await self._request_json(
            "DELETE", "/oapi/tweet_filter/delete_rule", {"rule_id": rule_id}
        )

    async def resolve_handle(self, handle: str) -> str | None:
        """Return user_id for a screen_name, or None if not found."""
        data = await self._get("/twitter/user/info", {"userName": handle})
        if not data:
            return None
        node = data.get("data") or data
        uid = node.get("id") or node.get("id_str") or node.get("user_id")
        return str(uid) if uid else None

    async def search_tweets(
        self,
        *,
        query: str,
        since_time_unix: int,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Wraps /twitter/tweet/advanced_search.

        Pricing: 15 credits per returned tweet, 15 credits floor when empty —
        so an idle handle's batch costs ~15 credits regardless of how many
        handles share the OR query. Returns a dict with `tweets`,
        `has_next_page`, `next_cursor` (string or empty)."""
        params: dict[str, Any] = {
            "query": query,
            "queryType": "Latest",
            "since_time": since_time_unix,
        }
        if cursor:
            params["cursor"] = cursor
        data = await self._get("/twitter/tweet/advanced_search", params)
        tweets = data.get("tweets")
        if not isinstance(tweets, list):
            inner = data.get("data") if isinstance(data.get("data"), dict) else {}
            tweets = inner.get("tweets") if isinstance(inner, dict) else []
        if not isinstance(tweets, list):
            tweets = []
        return {
            "tweets": tweets,
            "has_next_page": bool(data.get("has_next_page")),
            "next_cursor": str(data.get("next_cursor") or ""),
        }

    async def get_last_tweets(
        self, *, user_id: str, since_id: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Fetch recent tweets by a user. Filters client-side by since_id
        because TwitterAPI.io's filter param name has shifted historically;
        a local int compare is robust."""
        data = await self._get(
            "/twitter/user/last_tweets",
            {"userId": user_id, "limit": limit},
        )
        # Live API wraps tweets inside data["data"]["tweets"]; older mocked
        # tests used a flatter shape. Walk into the inner dict if present.
        inner = data.get("data") if isinstance(data.get("data"), dict) else data
        tweets = inner.get("tweets") if isinstance(inner, dict) else None
        if not isinstance(tweets, list):
            tweets = []
        if since_id is None:
            return list(tweets)
        try:
            cutoff = int(since_id)
        except ValueError:
            return list(tweets)
        return [t for t in tweets if int(t.get("id_str") or t.get("id") or 0) > cutoff]
