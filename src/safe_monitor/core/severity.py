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
    # "攻击"/"漏洞" picked up bare (not just "被攻击"/"协议漏洞") so phrases
    # like "遭遇跨链攻击" or "爆出严重漏洞" match. Real-world告警 from
    # WuBlockchain / zachxbt quotes use these forms — the strict patterns
    # silently dropped them. Noise risk (e.g. "网络攻击" in promo material)
    # is caught by the LLM classifier gate downstream.
    r"(被盗|被黑|攻击|漏洞|资金被盗|私钥泄露|跑路)"
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


def has_security_keywords(text: str | None) -> bool:
    """True if `text` matches any security-relevant keyword (hack / phishing /
    sanction, EN+ZH). Single source of truth for "does this look security-
    related at the keyword level" — shared by score() and tier-gating in the
    X tweet parser so the two stay in lockstep."""
    t = text or ""
    return _any(
        t,
        _HACK_WORDS_EN, _HACK_WORDS_ZH,
        _PHISH_WORDS_EN, _PHISH_WORDS_ZH,
        _SANCTION_WORDS_EN, _SANCTION_WORDS_ZH,
    )


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
