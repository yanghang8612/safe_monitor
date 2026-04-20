from __future__ import annotations

import re

from safe_monitor.core.parsers.generic_tg import parse_generic_tg

_LOSS_PATTERN = re.compile(
    r"(?:approximately|about|~)?\s*\$?([\d,]+(?:\.\d+)?)\s*(K|M|B)?\b", re.IGNORECASE
)


def parse_peckshield(text: str) -> dict:
    base = parse_generic_tg(text)
    # PeckShield alerts often say "Attack tx:" / "Attacker:" — generic already handles 0x/0x.
    # The generic parser skips attacker_addr if a tx hash is present, so extract
    # the labeled "Attacker: 0x<40>" form explicitly.
    if base.get("attacker_addr") is None:
        m_attacker = re.search(r"Attacker[:\s]+(0x[a-fA-F0-9]{40})\b", text)
        if m_attacker:
            base["attacker_addr"] = m_attacker.group(1)
    # Override loss parsing to prefer phrases like "approximately $X drained"
    m = re.search(r"(?:drained|stolen|loss[^$]*?)\$([\d,]+(?:\.\d+)?)\s*(K|M|B)?", text, re.IGNORECASE)
    if not m:
        # Fallback: PeckShield often phrases it as "$80M was drained" (keyword after amount)
        m = re.search(
            r"\$([\d,]+(?:\.\d+)?)\s*(K|M|B)?\b[^.\n]*?(?:drained|stolen)",
            text, re.IGNORECASE,
        )
    if m:
        num = float(m.group(1).replace(",", ""))
        mult = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get((m.group(2) or "").upper(), 1)
        base["loss_usd"] = num * mult
    return base
