from __future__ import annotations

import re

from safe_monitor.core.models import Severity

# English vocabulary uses \b word boundaries.
_HACK_WORDS_EN = re.compile(
    r"\b(hack|hacked|exploit|exploited|attack|attacked|drained|stolen|"
    r"compromised|backdoor|reentrancy|rugpull|rug\s+pull)\b",
    re.IGNORECASE,
)
# Chinese tokens: \b is unreliable because Chinese characters are all \w in
# Python re, so a CJK keyword surrounded by other CJK has no word boundary.
_HACK_WORDS_ZH = re.compile(
    r"(被盗|被黑|被攻击|资金被盗|私钥泄露|协议漏洞|跑路)"
)
_PHISH_WORDS_EN = re.compile(
    r"\b(phishing|drainer|approval\s+phishing)\b",
    re.IGNORECASE,
)
_PHISH_WORDS_ZH = re.compile(r"(钓鱼|被骗|盗号)")
_SANCTION_WORDS_EN = re.compile(r"\b(sanctioned|OFAC)\b", re.IGNORECASE)
_SANCTION_WORDS_ZH = re.compile(r"(制裁)")


def _any(text: str, *patterns: re.Pattern[str]) -> bool:
    return any(p.search(text) for p in patterns)


def score(text: str | None, loss_usd: float | None) -> Severity:
    t = text or ""
    hack = _any(t, _HACK_WORDS_EN, _HACK_WORDS_ZH)
    phish = _any(t, _PHISH_WORDS_EN, _PHISH_WORDS_ZH)
    sanction = _any(t, _SANCTION_WORDS_EN, _SANCTION_WORDS_ZH)

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
