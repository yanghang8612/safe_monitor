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
    assert p["title"].startswith("@WuBlockchain")


def test_parse_quoted_tweet_appended_to_body():
    # RTs/quotes: original tweet's preview text is truncated by Twitter; the full
    # content of the original lives in `quoted_tweet` and we should surface it.
    raw = {
        "id": "12345",
        "text": "RT @bax1337: KelpDAO/Arbitrum SDNY case update: court ruled Arbitrum DA…",
        "author": {"id": "1", "userName": "tayvano_"},
        "quoted_tweet": {
            "id": "67890",
            "text": "KelpDAO/Arbitrum SDNY case update: court ruled Arbitrum DAO can transfer the recovered ~$71M into a multisig managed by Aave & others.",
            "author": {"userName": "bax1337"},
        },
    }
    p = parse_x_tweet(raw, tier="S")
    # Title is just the handle; body holds RT preview + full quoted text.
    assert p["title"] == "@tayvano_"
    assert "$71M" in p["body"]
    assert "@bax1337:" in p["body"]


def test_parse_quoted_tweet_skipped_when_already_in_text():
    # If the preview text already contains the full quoted content, don't dup it.
    raw = {
        "id": "1",
        "text": "Quoted: Resolv exploited for 80M",
        "author": {"id": "1", "userName": "x"},
        "quoted_tweet": {"text": "Resolv exploited for 80M", "author": {"userName": "y"}},
    }
    p = parse_x_tweet(raw, tier="S")
    assert p["body"].count("Resolv exploited for 80M") == 1


def test_parse_missing_id_raises():
    import pytest as _pt
    with _pt.raises(ValueError):
        parse_x_tweet({"text": "hello"}, tier="S")
