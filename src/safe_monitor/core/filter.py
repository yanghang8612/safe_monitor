from __future__ import annotations

from safe_monitor.config import FilterCfg
from safe_monitor.core.models import Event, Severity
from safe_monitor.publishers.classifier import Classifier

_SEVERITY_MAP = {
    "low": Severity.low,
    "medium": Severity.medium,
    "high": Severity.high,
    "critical": Severity.critical,
}

# Sources whose items mix real incidents with opinion/analysis that
# keyword-match security but aren't actionable events (e.g. wublock_news
# carries CertiK-CEO interviews alongside actual hack reports). Routed
# through the LLM gate just like X.
_LLM_GATED_SOURCES = frozenset({"wublock_news"})


class Filter:
    def __init__(self, cfg: FilterCfg, classifier: Classifier | None = None):
        self._min = _SEVERITY_MAP[cfg.min_severity]
        self._deny = [k.lower() for k in cfg.deny_keywords]
        self._classifier = classifier

    async def allow(self, event: Event) -> bool:
        if event.severity < self._min:
            return False
        hay = f"{event.title} {event.body or ''}".lower()
        for kw in self._deny:
            if kw in hay:
                return False
        # Forta / OFAC / Rekt are pre-curated by their feed structure so we
        # trust them without the LLM gate. X tweets and wublock RSS items
        # need it.
        if self._classifier is not None and (
            event.source.startswith("x_") or event.source in _LLM_GATED_SOURCES
        ):
            text = f"{event.title}\n{event.body or ''}".strip()
            if not await self._classifier.is_security(text):
                return False
        return True
