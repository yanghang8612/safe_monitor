from safe_monitor.core.parsers.peckshield import parse_peckshield
from safe_monitor.core.parsers.slowmist import parse_slowmist
from safe_monitor.core.parsers.whale_alert import parse_whale_alert


def test_peckshield_extracts_fields():
    text = (
        "Hi @Resolvlabs, our system has detected a hack on Resolv. "
        "Attacker: 0x" + "a" * 40 + ". Attack tx: 0x" + "b" * 64 + ". "
        "Approximately $80M was drained from the USR vault on Ethereum."
    )
    r = parse_peckshield(text)
    assert r["tx_hash"].startswith("0x")
    assert r["attacker_addr"].startswith("0x")
    assert r["loss_usd"] == 80_000_000
    assert r["chain"] == "Ethereum"


def test_slowmist_handles_chinese():
    text = "慢雾安全提醒：Resolv 协议遭到攻击，USR 稳定币被盗，损失约 $80,000,000。链: Ethereum"
    r = parse_slowmist(text)
    assert r["loss_usd"] == 80_000_000
    assert r["chain"] == "Ethereum"


def test_whale_alert_extracts_amount():
    text = "🚨 1,000 #BTC (65,000,000 USD) transferred from unknown wallet to unknown wallet"
    r = parse_whale_alert(text)
    assert r["loss_usd"] == 65_000_000  # using USD figure as loss proxy
