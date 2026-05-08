from __future__ import annotations

from typing import Any

from safe_monitor.core.models import Severity

_SEV_MAP = {
    "CRITICAL": Severity.critical,
    "HIGH": Severity.high,
    "MEDIUM": Severity.medium,
    "LOW": Severity.low,
    "INFO": Severity.low,
}

# chainId → (display name, explorer base for tx URL).
# Covers the major chains Forta monitors today; unknowns fall through to None.
_CHAINS: dict[int, tuple[str, str]] = {
    1: ("Ethereum", "https://etherscan.io"),
    10: ("Optimism", "https://optimistic.etherscan.io"),
    56: ("BSC", "https://bscscan.com"),
    137: ("Polygon", "https://polygonscan.com"),
    250: ("Fantom", "https://ftmscan.com"),
    8453: ("Base", "https://basescan.org"),
    42161: ("Arbitrum", "https://arbiscan.io"),
    43114: ("Avalanche", "https://snowtrace.io"),
}


def _categorize(name: str, description: str) -> list[str]:
    """Map the alert text to our a–j event taxonomy. Heuristic — Forta alerts
    are bot-defined so titles/descriptions vary widely."""
    blob = f"{name} {description}".lower()
    out: list[str] = []
    if any(w in blob for w in ("flash loan", "exploit", "reentrancy", "oracle")):
        out.append("a")
    if any(w in blob for w in ("bridge", "cross-chain")):
        out.append("b")
    if any(w in blob for w in ("phishing", "drainer", "approval", "scam")):
        out.append("e")
    if any(w in blob for w in ("rugpull", "rug pull", "honeypot")):
        out.append("g")
    if any(w in blob for w in ("governance", "proposal")):
        out.append("j")
    return out or ["a"]  # default to "exploit" if nothing matches


def parse_forta(alert: dict[str, Any]) -> dict[str, Any]:
    name = (alert.get("name") or "").strip() or "Forta alert"
    protocol = (alert.get("protocol") or "").strip()
    description = (alert.get("description") or "").strip()
    chain_id = alert.get("chainId")
    src = alert.get("source") or {}
    tx_hash = (src.get("transactionHash") or "").strip() or None

    title = f"{name} — {protocol}" if protocol else name

    chain_name: str | None = None
    url: str | None = None
    if isinstance(chain_id, int) and chain_id in _CHAINS:
        chain_name, explorer = _CHAINS[chain_id]
        if tx_hash:
            url = f"{explorer}/tx/{tx_hash}"

    severity = _SEV_MAP.get((alert.get("severity") or "").upper())

    return {
        "title": title,
        "body": description or None,
        "url": url,
        "chain": chain_name,
        "tx_hash": tx_hash,
        "category": _categorize(name, description),
        "severity": severity,
    }
