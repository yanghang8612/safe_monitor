from safe_monitor.core.models import Severity
from safe_monitor.core.severity import score


def test_english_hack_keyword_no_loss():
    assert score("Protocol X has been exploited", None) == Severity.high


def test_english_hack_keyword_with_loss_million():
    assert score("Protocol X exploited", 5_000_000) == Severity.high


def test_english_hack_keyword_with_loss_ten_million():
    assert score("Protocol X exploited", 50_000_000) == Severity.critical


def test_chinese_hack_keyword():
    assert score("协议X被攻击，损失约500万美元", None) == Severity.high


def test_chinese_funds_stolen():
    assert score("某DeFi项目资金被盗", None) == Severity.high


def test_chinese_private_key_leak():
    assert score("用户私钥泄露导致钱包被清空", None) == Severity.high


def test_drainer_keyword():
    assert score("New wallet drainer targeting Uniswap users", None) == Severity.medium


def test_rugpull_compound_word():
    assert score("Rug pull alert: dev drained the pool", None) == Severity.high


def test_rugpull_single_word():
    assert score("Rugpull on token X", None) == Severity.high


def test_backdoor_keyword():
    assert score("Backdoor discovered in deployment script", None) == Severity.high


def test_reentrancy_keyword():
    assert score("Reentrancy bug exploited in lending protocol", None) == Severity.high


def test_approval_phishing():
    assert score("Approval phishing campaign on social media", None) == Severity.medium


def test_sanction_keyword():
    assert score("OFAC sanctioned mixer addresses", None) == Severity.high


def test_chinese_phishing_keyword():
    assert score("钓鱼网站冒充Uniswap官网", None) == Severity.medium


def test_neutral_text_low():
    assert score("Daily market update for Bitcoin", None) == Severity.low


def test_empty_text():
    assert score("", None) == Severity.low


def test_none_text():
    assert score(None, None) == Severity.low
