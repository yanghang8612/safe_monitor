from __future__ import annotations

from typing import Protocol

import structlog
from openai import APIError, APITimeoutError, AsyncOpenAI

log = structlog.get_logger(__name__)

_SYSTEM_PROMPT = (
    "You judge whether a message reports a REAL-TIME, ACTIONABLE Web3/crypto "
    "SECURITY INCIDENT — something the reader should react to right now.\n"
    "\n"
    "YES — an actual incident or freshly-published advisory:\n"
    "  - a specific protocol/address/user just got hacked, exploited, drained, "
    "or phished (ongoing or within hours)\n"
    "  - bridge attack, oracle manipulation, key/seed compromise, rug pull, "
    "stablecoin depeg in progress\n"
    "  - OFAC just sanctioned an address; an address was just designated\n"
    "  - postmortem or vulnerability disclosure tied to a specific deployed "
    "contract or incident\n"
    "  - on-chain forensic call-out naming a specific victim or attacker\n"
    "\n"
    "NO — anything else, even if security-adjacent:\n"
    "  - jokes, memes, POV/relatable posts, personal life, gaming\n"
    "  - opinion pieces, analyst commentary, industry-trend articles "
    "(e.g. 'AI is changing DeFi security'), CEO/researcher interviews\n"
    "  - generic warnings, educational threads, or 'be careful out there' "
    "without a specific named incident\n"
    "  - market-price talk, NFT/DeFi promos, governance proposals, hiring, "
    "conference announcements\n"
    "  - replies/quotes that are only emojis, mentions, or URLs with no "
    "incident text\n"
    "  - retweets or commentary about old/already-resolved incidents with no "
    "new development\n"
    "\n"
    "When in doubt — if it reads more like analysis, commentary, or "
    "background than a fresh event — answer NO.\n"
    "\n"
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
