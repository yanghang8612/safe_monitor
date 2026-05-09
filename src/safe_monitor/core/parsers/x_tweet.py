from __future__ import annotations

from typing import Any

from safe_monitor.core.models import Severity

_HIGH_TIERS = frozenset({"S", "A"})


def _categorize(text: str) -> list[str]:
    blob = text.lower()
    out: list[str] = []
    if any(w in blob for w in ("exploit", "hack", "drain", "stolen", "被盗", "被黑")):
        out.append("a")
    if "bridge" in blob or "cross-chain" in blob:
        out.append("b")
    if any(w in blob for w in ("phishing", "drainer", "approval", "钓鱼", "盗号")):
        out.append("e")
    if "rug" in blob or "rugpull" in blob or "跑路" in blob:
        out.append("g")
    if "stablecoin" in blob or "depeg" in blob or "脱锚" in blob:
        out.append("f")
    if "sanction" in blob or "ofac" in blob or "制裁" in blob:
        out.append("h")
    return out


def _canonical_url(user_screen: str, tweet_id: str) -> str:
    return f"https://x.com/{user_screen}/status/{tweet_id}"


def parse_x_tweet(tweet: dict[str, Any], *, tier: str) -> dict[str, Any]:
    """Parse a TwitterAPI.io tweet object (WS or REST shape).

    `tier` controls the severity hint:
      S/A  -> Severity.high  (skip keyword scorer in normalizer)
      else -> None           (normalizer falls back to keyword scorer)
    """
    tid = tweet.get("id_str") or tweet.get("id")
    if not tid:
        raise ValueError("tweet missing id_str / id")
    tid = str(tid)

    # TwitterAPI.io REST nests author info under "author" with camelCase
    # "userName"; the legacy/WS shape uses "user" with "screen_name". Cover both.
    user = tweet.get("author") or tweet.get("user") or {}
    screen = (
        user.get("userName")
        or user.get("screen_name")
        or user.get("username")
        or "unknown"
    )
    text = (tweet.get("text") or tweet.get("full_text") or "").strip()

    # When this tweet is an RT/quote, the visible `text` is Twitter's truncated
    # "RT @user: ..." preview. The full content of the original lives in the
    # `quoted_tweet` object — append it so the alert isn't missing the actual
    # information.
    quoted = tweet.get("quoted_tweet") or {}
    quoted_text = (quoted.get("text") or quoted.get("full_text") or "").strip()
    if quoted_text and quoted_text not in text:
        q_user = quoted.get("author") or quoted.get("user") or {}
        q_screen = q_user.get("userName") or q_user.get("screen_name") or ""
        prefix = f"@{q_screen}: " if q_screen else ""
        text = f"{text}\n\n— {prefix}{quoted_text}"

    # Always use the canonical x.com permalink — entities[urls] is for embedded
    # links in the tweet body, not the tweet's own permalink.
    url = _canonical_url(screen, tid)

    # Title is just the author handle — the full content lives in body.
    # Repeating a truncated copy in the title was duplication + visual noise.
    title = f"@{screen}"

    severity: Severity | None = Severity.high if tier in _HIGH_TIERS else None

    return {
        "title": title,
        "body": text,
        "url": url,
        "category": _categorize(text),
        "severity": severity,
        "user_screen_name": screen,
    }
