from __future__ import annotations

import re

from safe_monitor.core.models import Severity

_HACK_WORDS = re.compile(
    r"\b(hack|hacked|exploit|exploited|被盗|攻击|attack|drained|stolen|compromised)\b",
    re.IGNORECASE,
)
_PHISH_WORDS = re.compile(r"\b(phishing|drainer|钓鱼|scam)\b", re.IGNORECASE)
_SANCTION_WORDS = re.compile(r"\b(sanctioned|OFAC|制裁)\b", re.IGNORECASE)


def score(text: str, loss_usd: float | None) -> Severity:
    hack = bool(_HACK_WORDS.search(text or ""))
    phish = bool(_PHISH_WORDS.search(text or ""))
    sanction = bool(_SANCTION_WORDS.search(text or ""))

    if hack and loss_usd is not None:
        if loss_usd >= 10_000_000:
            return Severity.critical
        if loss_usd >= 1_000_000:
            return Severity.high
    if hack:
        return Severity.high
    if sanction:
        return Severity.high
    if phish:
        return Severity.medium
    return Severity.low
