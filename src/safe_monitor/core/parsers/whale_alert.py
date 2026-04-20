from __future__ import annotations

import re

from safe_monitor.core.parsers.generic_tg import parse_generic_tg

_USD = re.compile(r"\(([\d,]+(?:\.\d+)?)\s*USD\)", re.IGNORECASE)


def parse_whale_alert(text: str) -> dict:
    base = parse_generic_tg(text)
    m = _USD.search(text)
    if m:
        base["loss_usd"] = float(m.group(1).replace(",", ""))
    return base
