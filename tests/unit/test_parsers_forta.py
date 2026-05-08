import json
from pathlib import Path

from safe_monitor.core.models import Severity
from safe_monitor.core.parsers.forta import parse_forta


def _alerts():
    return json.loads(Path("tests/fixtures/forta_alerts_response.json").read_text())[
        "data"
    ]["alerts"]["alerts"]


def test_parse_critical_alert_extracts_core_fields():
    alert = _alerts()[0]
    p = parse_forta(alert)

    assert p["title"] == "Flash Loan Attack — ExampleLend"
    assert "flash loan attack detected" in p["body"].lower()
    assert p["tx_hash"] == "0xdeadbeef00000000000000000000000000000000000000000000000000000001"
    assert p["chain"] == "Ethereum"
    assert p["url"] == (
        "https://etherscan.io/tx/0xdeadbeef00000000000000000000000000000000000000000000000000000001"
    )
    assert "a" in p["category"]
    assert p["severity"] == Severity.critical


def test_parse_high_alert_severity_passthrough():
    alert = _alerts()[1]
    assert parse_forta(alert)["severity"] == Severity.high


def test_parse_unknown_severity_returns_none_hint():
    alert = {"name": "X", "severity": "WHATEVER", "createdAt": "2026-05-08T00:00:00Z"}
    assert parse_forta(alert)["severity"] is None


def test_parse_high_alert_no_protocol_falls_back_to_name():
    alert = _alerts()[1]
    p = parse_forta(alert)

    assert p["title"] == "Suspicious Approval"
    assert p["chain"] == "BSC"
    assert p["url"].startswith("https://bscscan.com/tx/")
    assert "e" in p["category"]  # phishing


def test_parse_unknown_chain_id_falls_back_gracefully():
    alert = {
        "hash": "0xabc",
        "name": "Generic Alert",
        "severity": "HIGH",
        "description": "something happened",
        "chainId": 99999,
        "createdAt": "2026-05-08T10:00:00Z",
        "source": {"transactionHash": "0xtx"},
    }
    p = parse_forta(alert)
    assert p["title"] == "Generic Alert"
    assert p["chain"] is None
    assert p["url"] is None  # unknown chain → no explorer URL
