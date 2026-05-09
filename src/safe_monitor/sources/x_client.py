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

    async def resolve_handle(self, handle: str) -> str | None:
        """Return user_id for a screen_name, or None if not found."""
        data = await self._get("/twitter/user/info", {"userName": handle})
        if not data:
            return None
        node = data.get("data") or data
        uid = node.get("id") or node.get("id_str") or node.get("user_id")
        return str(uid) if uid else None

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
