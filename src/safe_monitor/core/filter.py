from __future__ import annotations

from safe_monitor.config import FilterCfg
from safe_monitor.core.models import Event, Severity

_SEVERITY_MAP = {
    "low": Severity.low,
    "medium": Severity.medium,
    "high": Severity.high,
    "critical": Severity.critical,
}


class Filter:
    def __init__(self, cfg: FilterCfg):
        self._min = _SEVERITY_MAP[cfg.min_severity]
        self._deny = [k.lower() for k in cfg.deny_keywords]

    def allow(self, event: Event) -> bool:
        if event.severity < self._min:
            return False
        hay = f"{event.title} {event.body or ''}".lower()
        for kw in self._deny:
            if kw in hay:
                return False
        return True
