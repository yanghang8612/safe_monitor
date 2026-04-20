from __future__ import annotations

import re

from safe_monitor.core.models import EventCategory

_TX = re.compile(r"0x[a-fA-F0-9]{64}")
_ADDR = re.compile(r"0x[a-fA-F0-9]{40}")
_LOSS_USD = re.compile(
    r"(?:loss|stolen|drained|amount)[^$]*\$([\d,]+(?:\.\d+)?)\s*(K|M|B)?", re.IGNORECASE
)
_CHAIN = re.compile(
    r"\b(Ethereum|BSC|Polygon|Arbitrum|Optimism|Base|Solana|TRON|Avalanche|Bitcoin)\b",
    re.IGNORECASE,
)


def _normalize_loss(groups: tuple[str, str | None]) -> float:
    num = float(groups[0].replace(",", ""))
    mult = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get((groups[1] or "").upper(), 1)
    return num * mult


def parse_generic_tg(text: str) -> dict:
    tx_match = _TX.search(text)
    tx = tx_match.group(0) if tx_match else None
    attacker = None
    if not tx:
        # if no tx, first 0x40 may be an address
        a = _ADDR.search(text)
        attacker = a.group(0) if a else None
    loss_match = _LOSS_USD.search(text)
    loss = _normalize_loss(loss_match.groups()) if loss_match else None
    chain_match = _CHAIN.search(text)
    chain = chain_match.group(0) if chain_match else None

    cats: list[EventCategory] = []
    low = text.lower()
    if any(w in low for w in ("exploit", "hacked", "attack", "被盗", "drained", "stolen")):
        cats.append(EventCategory.a)
    if any(w in low for w in ("bridge",)):
        cats.append(EventCategory.b)
    if any(w in low for w in ("phishing", "drainer", "钓鱼", "scam")):
        cats.append(EventCategory.e)
    if "sanction" in low or "OFAC" in text:
        cats.append(EventCategory.h)

    title = text.split("\n", 1)[0][:200]
    return {
        "title": title,
        "body": text if len(text) > 200 else None,
        "tx_hash": tx,
        "attacker_addr": attacker,
        "loss_usd": loss,
        "chain": chain,
        "category": cats or [EventCategory.a],  # default
    }
