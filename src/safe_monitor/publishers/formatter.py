from __future__ import annotations

from safe_monitor.core.models import Event, Severity

_SEV_EMOJI = {
    Severity.critical: "🔴 CRITICAL",
    Severity.high: "🟠 HIGH",
    Severity.medium: "🟡 MEDIUM",
    Severity.low: "⚪ LOW",
}

_MAX_LEN = 4000


def _fmt_usd(v: float | None) -> str | None:
    if v is None:
        return None
    return f"${int(v):,} USD"


def _truncate(text: str) -> str:
    if len(text) <= _MAX_LEN:
        return text
    return text[: _MAX_LEN - 3] + "..."


def format_summary(e: Event) -> str:
    lines: list[str] = ["🚨 摘要 SUMMARY", e.title]
    if e.body:
        lines.append(e.body)
    return _truncate("\n".join(lines))


def format_details(e: Event) -> str:
    lines: list[str] = ["📋 详情 DETAILS"]

    def row(label_cn: str, label_en: str, value: str | None):
        if value:
            lines.append(f"- {label_cn} {label_en}: {value}")

    row("链", "Chain", e.chain)
    row("攻击者地址", "Attacker", e.attacker_addr)
    row("攻击交易", "Tx hash", e.tx_hash)
    row("预估损失", "Estimated loss", _fmt_usd(e.loss_usd))
    row("来源", "Source", e.source)
    row("链接", "Link", e.url)
    row("严重度", "Severity", _SEV_EMOJI.get(e.severity))
    row("时间", "Timestamp", e.received_at.strftime("%Y-%m-%d %H:%M:%S UTC"))
    return _truncate("\n".join(lines))


def format_event(e: Event) -> str:
    """Combined single-message format. Kept for tests / non-TG publishers."""
    return _truncate(format_summary(e) + "\n\n" + format_details(e))
