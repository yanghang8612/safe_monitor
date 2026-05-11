from __future__ import annotations

import re
from typing import Any

from safe_monitor.core.models import Severity

_HIGH_TIERS = frozenset({"S", "A"})

# Twitter prepends "@user1 @user2 ..." to every reply body. Once we surface
# "回复 @user1" in the title, those leading mentions are redundant noise.
_LEADING_MENTIONS_RE = re.compile(r"^((?:@[A-Za-z0-9_]+\s+)+)(\S.*)", re.DOTALL)
_ALL_MENTIONS_RE = re.compile(r"^(?:@[A-Za-z0-9_]+\s*)+$")


def _strip_leading_mentions(text: str) -> str:
    # If the body is *only* @-mentions, leave it alone — stripping would
    # erase everything (or the last handle, which is just as useless).
    if _ALL_MENTIONS_RE.match(text.strip()):
        return text
    m = _LEADING_MENTIONS_RE.match(text)
    return m.group(2) if m else text


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


def _screen_of(node: dict[str, Any] | None) -> str:
    if not isinstance(node, dict):
        return ""
    user = node.get("author") or node.get("user") or {}
    if not isinstance(user, dict):
        return ""
    return (
        user.get("userName")
        or user.get("screen_name")
        or user.get("username")
        or ""
    )


def _display_name_of(node: dict[str, Any] | None) -> str:
    if not isinstance(node, dict):
        return ""
    user = node.get("author") or node.get("user") or {}
    if not isinstance(user, dict):
        return ""
    name = user.get("name") or user.get("displayName") or ""
    return str(name).strip()


def _author_label(screen: str, display_name: str) -> str:
    """Render the author for a tweet title.

    "Display Name (@handle)" when the two differ — both pieces matter for
    a quick scan. Collapses to "@handle" when they're the same (or when
    no display name is set), to avoid noisy "samczsun (@samczsun)".
    """
    if display_name and display_name.lower() != screen.lower():
        return f"{display_name} (@{screen})"
    return f"@{screen}"


def _text_of(node: dict[str, Any] | None) -> str:
    if not isinstance(node, dict):
        return ""
    return (node.get("text") or node.get("full_text") or "").strip()


def _is_reply(tweet: dict[str, Any]) -> bool:
    if tweet.get("isReply") is True:
        return True
    for k in ("inReplyToId", "in_reply_to_status_id", "in_reply_to_status_id_str"):
        if tweet.get(k):
            return True
    return False


def _reply_target(tweet: dict[str, Any]) -> str:
    return (
        tweet.get("inReplyToUsername")
        or tweet.get("in_reply_to_screen_name")
        or ""
    )


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

    screen = _screen_of(tweet) or "unknown"
    display_name = _display_name_of(tweet)
    author = _author_label(screen, display_name)
    own_text = _text_of(tweet)

    retweeted = tweet.get("retweeted_tweet") if isinstance(tweet.get("retweeted_tweet"), dict) else None
    quoted = tweet.get("quoted_tweet") if isinstance(tweet.get("quoted_tweet"), dict) else None

    # Type detection priority: retweet > reply > quote > original.
    # A tweet can technically have multiple markers (e.g. quote inside a
    # retweet); we surface the outermost relationship since that's what the
    # reader sees first.
    if retweeted:
        orig_screen = _screen_of(retweeted)
        orig_text = _text_of(retweeted)
        if orig_screen:
            title = f"{author} 🔁 转推 @{orig_screen}"
        else:
            title = f"{author} 🔁 转推"
        body = orig_text or own_text
    elif _is_reply(tweet):
        target = _reply_target(tweet)
        title = f"{author} 💬 回复 @{target}" if target else f"{author} 💬 回复"
        # Twitter prepends @-mentions of every thread participant to the
        # reply text — strip them since the title already names the target.
        body = _strip_leading_mentions(own_text)
        # The polling source fetches the parent and attaches it as
        # `_in_reply_to_tweet`. If missing (fetch failed or skipped), we
        # still mark the reply but can't show context.
        parent = tweet.get("_in_reply_to_tweet")
        if isinstance(parent, dict):
            p_screen = _screen_of(parent) or target
            p_text = _text_of(parent)
            if p_text and p_text not in body:
                prefix = f"@{p_screen}: " if p_screen else ""
                body = f"{body}\n\n↩️ {prefix}{p_text}"
    elif quoted:
        q_screen = _screen_of(quoted)
        q_text = _text_of(quoted)
        title = f"{author} 💭 引用 @{q_screen}" if q_screen else f"{author} 💭 引用"
        body = own_text
        if q_text and q_text not in body:
            prefix = f"@{q_screen}: " if q_screen else ""
            body = f"{body}\n\n💬 {prefix}{q_text}" if body else f"💬 {prefix}{q_text}"
    else:
        title = author
        body = own_text

    # Canonical permalink points at the visible tweet (the RT/reply/quote
    # itself), not the original — fingerprint stability depends on it.
    url = _canonical_url(screen, tid)
    severity: Severity | None = Severity.high if tier in _HIGH_TIERS else None

    return {
        "title": title,
        "body": body,
        "url": url,
        "category": _categorize(body),
        "severity": severity,
        "user_screen_name": screen,
    }
