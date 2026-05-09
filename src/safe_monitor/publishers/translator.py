from __future__ import annotations

import asyncio
from typing import Protocol

import structlog
from openai import AsyncOpenAI
from openai import APIError, APITimeoutError

log = structlog.get_logger(__name__)

_SYSTEM_PROMPT = (
    "You translate Web3 security alerts into Simplified Chinese. Rules:\n"
    "- Preserve technical names verbatim (LayerZero, MEV, Uniswap, OFAC, CVE-IDs, etc.).\n"
    "- Preserve addresses (0x...), tx hashes, URLs and USD figures unchanged.\n"
    "- Keep the tone factual and concise; no commentary or preamble.\n"
    "- Return ONLY the translated text, no explanation, no quotes."
)


def is_mostly_chinese(text: str, threshold: float = 0.3) -> bool:
    """Return True when ≥`threshold` fraction of letter-class chars are CJK ideographs."""
    if not text:
        return False
    chinese = 0
    letters = 0
    for ch in text:
        is_cjk = "一" <= ch <= "鿿"
        if is_cjk:
            chinese += 1
            letters += 1
        elif ch.isalpha():
            letters += 1
    if letters == 0:
        return False
    return chinese / letters >= threshold


class Translator(Protocol):
    async def translate(self, text: str) -> str: ...


class OpenAITranslator:
    """Translator over the OpenAI chat-completions API. Works against any
    OpenAI-compatible endpoint by passing `base_url` (e.g. DeepSeek:
    `https://api.deepseek.com`)."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str | None = None,
        timeout: float = 8.0,
    ):
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self._model = model

    async def translate(self, text: str) -> str:
        """Translate `text` to Simplified Chinese. Falls back to original on any
        OpenAI failure — translation must never block alert delivery."""
        if not text or not text.strip():
            return text
        try:
            resp = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                ],
                temperature=0.0,
            )
            out = (resp.choices[0].message.content or "").strip()
            return out or text
        except (APIError, APITimeoutError, asyncio.TimeoutError) as e:
            log.warning("translator.failed", error=str(e)[:120])
            return text
        except Exception as e:
            # Defensive: never let translation kill an alert.
            log.warning("translator.unexpected", error=str(e)[:120])
            return text
