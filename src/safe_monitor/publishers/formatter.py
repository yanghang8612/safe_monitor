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


_DIVIDER = "━━━━━━━━━━━━━━━━━━"


def format_event(e: Event) -> str:
    lines: list[str] = []
    # Leading divider so adjacent TG messages have a clear visual boundary;
    # without it, each alert flows directly into the next on the client.
    lines.append(_DIVIDER)
    lines.append("🚨 摘要 SUMMARY")
    lines.append(e.title)
    if e.body:
        lines.append(e.body)

    lines.append("")
    lines.append("📋 详情 DETAILS")

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

    text = "\n".join(lines)
    if len(text) > _MAX_LEN:
        text = text[: _MAX_LEN - 3] + "..."
    return text
