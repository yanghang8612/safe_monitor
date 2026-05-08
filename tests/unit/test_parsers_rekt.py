from safe_monitor.core.models import Severity
from safe_monitor.core.parsers.rekt import parse_rekt


def test_parse_basic_post():
    raw = {
        "title": "Example Bridge - Rekt",
        "link": "https://rekt.news/example-bridge-rekt",
        "description": "<p>Bridge exploited for $42M</p>",
    }
    p = parse_rekt(raw)
    assert p["title"] == "Example Bridge - Rekt"
    assert p["url"] == "https://rekt.news/example-bridge-rekt"
    assert "$42M" in p["body"]
    assert "<p>" not in p["body"]
    assert "a" in p["category"]


def test_parse_bridge_post_categorized_as_b():
    raw = {
        "title": "Some Bridge - Rekt",
        "link": "https://rekt.news/x",
        "description": "Bridge attack across Ethereum and Polygon",
    }
    assert "b" in parse_rekt(raw)["category"]


def test_parse_missing_description_uses_title_as_body():
    raw = {"title": "X - Rekt", "link": "https://rekt.news/x", "description": ""}
    p = parse_rekt(raw)
    assert p["body"] == "X - Rekt"


def test_parse_strips_html_entities():
    raw = {
        "title": "T",
        "link": "https://rekt.news/t",
        "description": "<p>Loss of $1M &amp; counting</p>",
    }
    p = parse_rekt(raw)
    assert "& counting" in p["body"]
    assert "&amp;" not in p["body"]


def test_parse_always_returns_high_severity_hint():
    raw = {"title": "boring", "link": "https://rekt.news/x", "description": "text"}
    assert parse_rekt(raw)["severity"] == Severity.high
