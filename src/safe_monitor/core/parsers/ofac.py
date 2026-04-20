from __future__ import annotations

from typing import Any

from safe_monitor.core.models import EventCategory


def parse_ofac(raw: dict[str, Any]) -> dict[str, Any]:
    """Parse an OFAC SDN entry extracted by the poller.

    The poller yields dicts with `uid`, `name`, `id_type`, `address` keys.
    """
    name = str(raw.get("name", "(unknown)"))
    id_type = str(raw.get("id_type", ""))
    address = str(raw.get("address", ""))

    title = f"OFAC sanction added: {name} ({id_type}) {address}"
    if len(title) > 200:
        title = title[:200]

    return {
        "title": title,
        "body": None,
        "url": None,
        "loss_usd": None,
        "chain": None,
        "tx_hash": None,
        "attacker_addr": None,
        "occurred_at": None,
        "category": [EventCategory.h],
    }
