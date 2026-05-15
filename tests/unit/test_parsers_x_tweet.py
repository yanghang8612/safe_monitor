import json
from pathlib import Path
from safe_monitor.core.parsers.x_tweet import parse_x_tweet
from safe_monitor.core.models import Severity


def _load(name: str) -> dict:
    return json.loads(Path(f"tests/fixtures/{name}").read_text())


def test_parse_ws_fast_tweet_extracts_url_and_categorizes():
    raw = _load("x_tweet_ws_fast_tweet.json")["tweet"]
    p = parse_x_tweet(raw, tier="S")
    assert p["title"].startswith("@samczsun")
    assert "EXPLOIT" in p["body"]
    assert p["url"] == "https://x.com/samczsun/status/1789012345678901234"
    assert "a" in p["category"]
    # Tier S forces high
    assert p["severity"] == Severity.high


def test_parse_rest_tweet_no_entities_falls_back_to_canonical_url():
    raw = _load("x_tweet_rest_response.json")["tweets"][0]
    p = parse_x_tweet(raw, tier="A")
    assert p["url"] == "https://x.com/PeckShieldAlert/status/1789012345678901111"
    assert p["severity"] == Severity.high


def test_parse_tier_b_uses_keyword_scorer():
    raw = {
        "id_str": "999",
        "text": "Big transfer detected: 100,000 ETH",
        "user": {"id_str": "1", "screen_name": "whale_alert", "name": "Whale Alert"},
    }
    p = parse_x_tweet(raw, tier="B")
    # No exploit/sanction keywords -> normalizer falls through to keyword scorer.
    assert p["severity"] is None


def test_parse_chinese_tier_d_emits_zh_keyword_match():
    raw = {
        "id_str": "1000",
        "text": "突发：某协议被黑，资金被盗约 500 万美元",
        "user": {"id_str": "2", "screen_name": "WuBlockchain", "name": "吴说"},
    }
    p = parse_x_tweet(raw, tier="D")
    # Tier D: severity hint is None so the scorer runs in normalizer.
    assert p["severity"] is None
    # Display name "吴说" differs from handle, so both surface in the title.
    assert p["title"] == "吴说 (@WuBlockchain)"


def test_parse_title_uses_display_name_plus_handle():
    # When display name differs from handle, surface both so the alert is
    # readable at a glance: "Taylor Monahan (@tayvano_)" reads better than
    # an opaque "@tayvano_".
    raw = {
        "id": "1",
        "text": "Just shipped a thing.",
        "author": {"id": "1", "userName": "tayvano_", "name": "Taylor Monahan"},
    }
    p = parse_x_tweet(raw, tier="S")
    assert p["title"] == "Taylor Monahan (@tayvano_)"


def test_parse_title_collapses_when_display_name_equals_handle():
    # Avoid duplication like "samczsun (@samczsun)" when the user hasn't
    # set a distinct display name.
    raw = {
        "id": "1",
        "text": "x",
        "author": {"id": "1", "userName": "samczsun", "name": "samczsun"},
    }
    p = parse_x_tweet(raw, tier="S")
    assert p["title"] == "@samczsun"


def test_parse_reply_strips_leading_at_mentions_in_body():
    # Twitter reply text always starts with @-mentions of the thread
    # participants. Once the title already says "回复 @jpthor", those
    # leading @-mentions are pure noise.
    raw = {
        "id": "100",
        "text": "@jpthor @jack actual content here",
        "author": {"id": "1", "userName": "tayvano_", "name": "Taylor Monahan"},
        "isReply": True,
        "inReplyToUsername": "jpthor",
    }
    p = parse_x_tweet(raw, tier="S")
    assert p["title"] == "Taylor Monahan (@tayvano_) 💬 回复 @jpthor"
    assert p["body"] == "actual content here"


def test_parse_reply_keeps_body_when_only_mentions():
    # Don't strip away the entire body if it's just @-mentions — leave it
    # so the reader still sees something. (Rare edge case.)
    raw = {
        "id": "100",
        "text": "@jpthor @jack",
        "author": {"id": "1", "userName": "x"},
        "isReply": True,
        "inReplyToUsername": "jpthor",
    }
    p = parse_x_tweet(raw, tier="S")
    assert p["body"] == "@jpthor @jack"


def test_parse_quoted_tweet_marked_and_appended():
    # Quote tweets: the author's own commentary + the quoted original. Title
    # marks the type with 💭 plus the quoted author; body contains both.
    raw = {
        "id": "12345",
        "text": "Important update on the KelpDAO case:",
        "author": {"id": "1", "userName": "tayvano_"},
        "quoted_tweet": {
            "id": "67890",
            "text": "KelpDAO/Arbitrum SDNY case update: court ruled Arbitrum DAO can transfer the recovered ~$71M into a multisig managed by Aave & others.",
            "author": {"userName": "bax1337"},
        },
    }
    p = parse_x_tweet(raw, tier="S")
    assert p["title"] == "@tayvano_ 💭 引用 @bax1337"
    assert "Important update" in p["body"]
    assert "$71M" in p["body"]
    assert "@bax1337" in p["body"]


def test_parse_quoted_tweet_skipped_when_already_in_text():
    # If the author's commentary already contains the full quoted content,
    # don't duplicate it in the body.
    raw = {
        "id": "1",
        "text": "Quoted: Resolv exploited for 80M",
        "author": {"id": "1", "userName": "x"},
        "quoted_tweet": {"text": "Resolv exploited for 80M", "author": {"userName": "y"}},
    }
    p = parse_x_tweet(raw, tier="S")
    assert p["body"].count("Resolv exploited for 80M") == 1


def test_parse_retweet_uses_original_text_not_truncated_preview():
    # Native RT: Twitter's preview text is "RT @x: ..." truncated. Real
    # content lives in `retweeted_tweet`. Body must use the original, and
    # the title should mark the relay with 🔁 plus the original author.
    raw = {
        "id": "55",
        "text": "RT @samczsun: EXPLOIT in progress on protocol X — drained ~$1…",
        "author": {"id": "9", "userName": "tayvano_"},
        "retweeted_tweet": {
            "id": "44",
            "text": "EXPLOIT in progress on protocol X — drained ~$12M, attacker addr 0xabc...",
            "author": {"userName": "samczsun"},
        },
    }
    p = parse_x_tweet(raw, tier="S")
    assert p["title"] == "@tayvano_ 🔁 转推 @samczsun"
    assert "$12M" in p["body"]
    # The truncated RT preview should not leak into the body.
    assert "RT @" not in p["body"]
    # Keyword categorization should run against the expanded body.
    assert "a" in p["category"]


def test_parse_reply_marks_title_and_appends_parent_when_present():
    # Reply: text is the author's reply; parent context is fetched by the
    # source and attached as `_in_reply_to_tweet`. The parser surfaces both.
    raw = {
        "id": "100",
        "text": "Confirmed — funds traced to Tornado Cash via 2 hops.",
        "author": {"id": "9", "userName": "zachxbt"},
        "isReply": True,
        "inReplyToUsername": "PeckShieldAlert",
        "inReplyToId": "99",
        "_in_reply_to_tweet": {
            "id": "99",
            "text": "Flashloan exploit on protocol Y, ~$3M loss. Attacker: 0xabc",
            "author": {"userName": "PeckShieldAlert"},
        },
    }
    p = parse_x_tweet(raw, tier="S")
    assert p["title"] == "@zachxbt 💬 回复 @PeckShieldAlert"
    assert "Confirmed" in p["body"]
    assert "Flashloan exploit" in p["body"]
    assert "@PeckShieldAlert" in p["body"]


def test_parse_reply_without_parent_still_marks_title():
    # If parent fetch failed or wasn't attempted, still mark the reply so the
    # reader knows it's not a standalone tweet.
    raw = {
        "id": "100",
        "text": "Agreed.",
        "author": {"id": "9", "userName": "zachxbt"},
        "isReply": True,
        "inReplyToUsername": "samczsun",
    }
    p = parse_x_tweet(raw, tier="S")
    assert p["title"] == "@zachxbt 💬 回复 @samczsun"
    assert p["body"] == "Agreed."


def test_parse_reply_snake_case_fields():
    # Legacy REST shape uses snake_case `in_reply_to_screen_name`.
    raw = {
        "id_str": "100",
        "text": "Agreed.",
        "user": {"id_str": "9", "screen_name": "zachxbt"},
        "in_reply_to_status_id_str": "99",
        "in_reply_to_screen_name": "samczsun",
    }
    p = parse_x_tweet(raw, tier="S")
    assert p["title"] == "@zachxbt 💬 回复 @samczsun"


def test_parse_original_tweet_title_unchanged():
    # No retweet, reply, or quote markers — keep the bare-handle title.
    raw = {
        "id": "1",
        "text": "Just shipped a thing.",
        "author": {"id": "1", "userName": "samczsun"},
    }
    p = parse_x_tweet(raw, tier="S")
    assert p["title"] == "@samczsun"


def test_parse_missing_id_raises():
    import pytest as _pt
    with _pt.raises(ValueError):
        parse_x_tweet({"text": "hello"}, tier="S")


def test_tier_s_without_security_keywords_drops_to_low():
    # pcaversaccio-style off-topic technical chatter: tier S but no incident
    # vocabulary -> severity.low so filter's min_severity=medium drops it
    # before the LLM gate runs (saves a classifier call on obvious chaff).
    raw = {
        "id": "1",
        "text": "所以我认为这在技术上并不准确；隐私池未来某天会推出？",
        "author": {"id": "1", "userName": "pcaversaccio"},
    }
    p = parse_x_tweet(raw, tier="S")
    assert p["severity"] == Severity.low


def test_tier_s_joke_with_security_word_passes_to_classifier():
    # pashov 段子 contains "漏洞" — a real security keyword. The parser
    # can't distinguish a joke from a real disclosure at the lexical layer,
    # so it keeps severity.high; the LLM classifier gate downstream (with
    # its "jokes / POV posts -> NO" rule) is what filters this case.
    raw = {
        "id": "10",
        "text": "POV：你终于找到了那个Critical漏洞，拿到了赏金💰",
        "author": {"id": "1", "userName": "pashov"},
    }
    p = parse_x_tweet(raw, tier="S")
    assert p["severity"] == Severity.high


def test_tier_s_with_security_keywords_keeps_high():
    # zachxbt 引用真实事件: keywords hit, tier S preserves the high boost.
    raw = {
        "id": "2",
        "text": "Thorchain似乎遭遇了跨链攻击，损失超过740万美元",
        "author": {"id": "9", "userName": "zachxbt"},
    }
    p = parse_x_tweet(raw, tier="S")
    assert p["severity"] == Severity.high


def test_tier_s_empty_reply_drops_to_low():
    # frangio_ replying with only a URL — no body content to match keywords.
    raw = {
        "id": "3",
        "text": "@real_philogy https://t.co/abc",
        "author": {"id": "1", "userName": "frangio_"},
        "isReply": True,
        "inReplyToUsername": "real_philogy",
    }
    p = parse_x_tweet(raw, tier="S")
    assert p["severity"] == Severity.low
