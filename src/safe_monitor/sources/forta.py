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


_QUERY = """
query Alerts($createdSince: BigInteger) {
  alerts(input: {
    severities: ["CRITICAL", "HIGH"],
    first: 50,
    blockSortDirection: DESC,
    createdSince: $createdSince
  }) {
    alerts {
      hash
      name
      description
      severity
      protocol
      addresses
      chainId
      createdAt
      source {
        transactionHash
      }
    }
  }
}
""".strip()


def _parse_iso_to_epoch_ms(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        # Forta returns RFC3339 with nanoseconds; Python only handles microseconds.
        # Truncate the fractional part to 6 digits before parsing.
        s = iso.rstrip("Z")
        if "." in s:
            head, frac = s.split(".", 1)
            frac = (frac + "000000")[:6]
            s = f"{head}.{frac}"
        dt = datetime.fromisoformat(s).replace(tzinfo=UTC)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return None


class FortaPoller(Source):
    """Polls Forta Network's public GraphQL endpoint for HIGH/CRITICAL alerts.

    Cursor: epoch milliseconds of the most recently emitted alert's createdAt.
    First run seeds the cursor to the latest alert's timestamp without
    emitting; subsequent runs only emit alerts newer than the cursor.
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
        self._endpoint = endpoint
        self._interval = poll_interval_seconds
        self._db = db

    async def _fetch(self, created_since: int | None) -> list[dict[str, Any]]:
        payload = {
            "query": _QUERY,
            "variables": {"createdSince": created_since},
        }
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.post(self._endpoint, json=payload)
            r.raise_for_status()
            data = r.json()
        if "errors" in data:
            log.warning("forta.graphql_errors", errors=data["errors"])
            return []
        return ((data.get("data") or {}).get("alerts") or {}).get("alerts") or []

    async def poll_once(self, sink: asyncio.Queue[RawEvent]) -> None:
        cp = await self._db.get_checkpoint(self.name)
        cursor_ms: int | None = None
        if cp and cp.get("cursor"):
            try:
                cursor_ms = int(cp["cursor"])
            except ValueError:
                cursor_ms = None
        first_run = cursor_ms is None

        alerts = await self._fetch(cursor_ms)
        if not alerts:
            log.info("forta.poll_done", emitted=0, first_run=first_run)
            return

        latest_ms = cursor_ms or 0
        emitted = 0
        for a in alerts:
            ts_ms = _parse_iso_to_epoch_ms(a.get("createdAt"))
            if ts_ms is None:
                continue
            if cursor_ms is not None and ts_ms <= cursor_ms:
                continue
            if ts_ms > latest_ms:
                latest_ms = ts_ms

            if first_run:
                # Seed-only: skip emission, just track latest timestamp.
                continue

            ev = RawEvent(
                source=self.name,
                source_kind="api",
                external_id=a.get("hash") or f"{ts_ms}",
                received_at=datetime.now(UTC),
                raw=a,
                text=a.get("description") or a.get("name") or "",
            )
            await sink.put(ev)
            emitted += 1

        if latest_ms and latest_ms != cursor_ms:
            await self._db.set_checkpoint(
                self.name, kind="api_poll", cursor=str(latest_ms)
            )
        log.info(
            "forta.poll_done",
            emitted=emitted,
            first_run=first_run,
            cursor_ms=latest_ms or None,
        )

    async def run(self, sink: asyncio.Queue[RawEvent]) -> None:
        while True:
            try:
                await self.poll_once(sink)
            except Exception as e:
                log.warning("forta.poll_error", error=str(e))
            await asyncio.sleep(self._interval)
