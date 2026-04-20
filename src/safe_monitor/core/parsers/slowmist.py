from __future__ import annotations

import re

from safe_monitor.core.parsers.generic_tg import parse_generic_tg

_CN_CHAIN = {"以太坊": "Ethereum", "比特币": "Bitcoin", "波场": "TRON", "币安链": "BSC"}


def parse_slowmist(text: str) -> dict:
    base = parse_generic_tg(text)
    if base.get("chain") is None:
        for cn, en in _CN_CHAIN.items():
            if cn in text:
                base["chain"] = en
                break
    # Prefer the more specific "损失约 $X" / "被盗 $X" pattern
    m = re.search(r"(?:损失约|被盗|损失)\s*\$?([\d,]+(?:\.\d+)?)\s*(K|M|B)?", text, re.IGNORECASE)
    if m:
        num = float(m.group(1).replace(",", ""))
        mult = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get((m.group(2) or "").upper(), 1)
        base["loss_usd"] = num * mult
    return base
