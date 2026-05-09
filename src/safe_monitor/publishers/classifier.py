from __future__ import annotations

from typing import Protocol

import structlog
from openai import APIError, APITimeoutError, AsyncOpenAI

log = structlog.get_logger(__name__)

_SYSTEM_PROMPT = (
    "You judge whether a tweet is about Web3 / crypto SECURITY.\n"
    "YES — hacks, exploits, drainers, phishing, rug pulls, depegs, OFAC sanctions, "
    "bridge attacks, key compromises, on-chain forensic analysis, smart contract bugs, "
    "audit findings, vulnerability disclosures, postmortems, security advisories.\n"
    "NO — gaming, sports, memes, personal life, market price action without security angle, "
    "NFT/DeFi promos, governance proposals, hiring, conference announcements, retweets of "
    "non-security content.\n"
    "Answer with exactly one token: YES or NO."
)


class Classifier(Protocol):
    async def is_security(self, text: str) -> bool: ...


class WebSecurityClassifier:
    """Yes/no classifier built on any OpenAI-compatible chat-completions endpoint
    (DeepSeek by default; works with OpenAI proper too). Fails OPEN on any error
    so a classifier outage never silently drops alerts."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "deepseek-chat",
        base_url: str | None = None,
        timeout: float = 5.0,
    ):
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self._model = model

    async def is_security(self, text: str) -> bool:
        if not text or not text.strip():
            return False
        try:
            resp = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": text[:1500]},  # cap input cost
                ],
                temperature=0.0,
                max_tokens=4,
            )
            out = (resp.choices[0].message.content or "").strip().upper()
            return out.startswith("Y")
        except (APIError, APITimeoutError) as e:
            log.warning("classifier.failed_open", error=str(e)[:120])
            return True
        except Exception as e:
            log.warning("classifier.unexpected_open", error=str(e)[:120])
            return True
