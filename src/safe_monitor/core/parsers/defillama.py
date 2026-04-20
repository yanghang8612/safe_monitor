from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from safe_monitor.core.models import EventCategory


def parse_defillama(raw: dict[str, Any]) -> dict[str, Any]:
    name = str(raw.get("name", "(unknown)"))
    loss = raw.get("amount")
    chains = raw.get("chains") or []
    chain = chains[0] if chains else None
    technique = raw.get("technique") or raw.get("classification") or ""
    ts = raw.get("date")
    occurred = datetime.fromtimestamp(int(ts), tz=UTC) if isinstance(ts, (int, float)) else None

    title = (
        f"DeFi hack: {name} — loss ${int(loss):,}"
        if isinstance(loss, (int, float))
        else f"DeFi hack: {name}"
    )
    body = f"Technique: {technique}" if technique else None
    return {
        "title": title,
        "body": body,
        "url": raw.get("link"),
        "loss_usd": float(loss) if isinstance(loss, (int, float)) else None,
        "chain": chain,
        "occurred_at": occurred,
        "category": [EventCategory.a],
    }
