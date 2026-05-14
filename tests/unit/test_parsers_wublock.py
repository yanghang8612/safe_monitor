from safe_monitor.core.parsers.wublock import parse_wublock
from safe_monitor.core.severity import score as score_severity


def _raw(title: str, description: str, link: str) -> dict:
    return {"title": title, "description": description, "link": link, "guid": link}


def test_parse_wublock_keeps_real_title_and_strips_body():
    raw = _raw(
        "某 DeFi 协议遭攻击被盗约 1200 万美元",
        "<p style=\"line-height: 1.75;\">吴说获悉，某 DeFi 协议遭攻击，攻击者通过协议漏洞被盗约 1200 万美元。</p>",
        "https://www.wublock123.com/news/defi-exploit-61067",
    )
    parsed = parse_wublock(raw)
    # title is the headline, not the first line of the body
    assert parsed["title"] == "某 DeFi 协议遭攻击被盗约 1200 万美元"
    # body stripped of HTML tags
    assert "<p" not in parsed["body"]
    assert "协议漏洞" in parsed["body"]
    assert parsed["url"] == "https://www.wublock123.com/news/defi-exploit-61067"
    assert isinstance(parsed["category"], list)
    # parser does NOT force a severity hint — scorer decides
    assert parsed.get("severity") is None


def test_parse_wublock_security_item_scores_high():
    raw = _raw(
        "某 DeFi 协议遭攻击被盗约 1200 万美元",
        "<p>吴说获悉，某 DeFi 协议遭攻击，攻击者通过协议漏洞被盗约 1200 万美元。</p>",
        "https://www.wublock123.com/news/defi-exploit-61067",
    )
    parsed = parse_wublock(raw)
    sev = score_severity(f"{parsed['title']} {parsed['body']}", parsed.get("loss_usd"))
    assert sev.name == "high"


def test_parse_wublock_funding_item_scores_low():
    raw = _raw(
        "金融基础设施公司 Stitch 宣布完成 2500 万美元 A 轮融资",
        "<p>吴说获悉，金融基础设施公司 Stitch 宣布完成 2500 万美元 A 轮融资，由 a16z 领投。</p>",
        "https://www.wublock123.com/news/stitch-61068",
    )
    parsed = parse_wublock(raw)
    sev = score_severity(f"{parsed['title']} {parsed['body']}", parsed.get("loss_usd"))
    # non-security news must fall to 'low' so min_severity=medium filters it
    assert sev.name == "low"
