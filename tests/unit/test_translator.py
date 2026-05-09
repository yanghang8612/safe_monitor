from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from safe_monitor.publishers.translator import OpenAITranslator, is_mostly_chinese


def test_is_mostly_chinese_true_for_chinese_text():
    assert is_mostly_chinese("PeckShield 报告：Resolv 协议被攻击")


def test_is_mostly_chinese_false_for_english_text():
    assert not is_mostly_chinese("Resolv protocol exploited for $80M on Ethereum")


def test_is_mostly_chinese_false_for_empty_text():
    assert not is_mostly_chinese("")
    assert not is_mostly_chinese("   ")


def test_is_mostly_chinese_handles_punctuation_and_addresses():
    # Mostly addresses/punctuation; no language signal -> False
    assert not is_mostly_chinese("0xabc...123 - $42M loss; tx 0xdead")


def test_is_mostly_chinese_handles_mixed_content():
    # 13% Chinese chars by letter ratio -> below default 0.3 threshold; should NOT translate.
    assert not is_mostly_chinese("被盗 exploited for $42M on Ethereum")
    # Chinese-dominant text with English brand names -> above threshold; skip translation.
    assert is_mostly_chinese("Resolv 协议被攻击者利用闪电贷攻击，损失 8000 万美元")


@pytest.mark.asyncio
async def test_translator_returns_translated_content():
    fake_choice = MagicMock()
    fake_choice.message.content = "测试译文"
    fake_resp = MagicMock(choices=[fake_choice])

    t = OpenAITranslator(api_key="sk-test")
    t._client.chat.completions.create = AsyncMock(return_value=fake_resp)

    out = await t.translate("test source")
    assert out == "测试译文"


@pytest.mark.asyncio
async def test_translator_falls_back_to_original_on_error():
    t = OpenAITranslator(api_key="sk-test")
    t._client.chat.completions.create = AsyncMock(side_effect=RuntimeError("boom"))
    out = await t.translate("original text")
    assert out == "original text"


def test_translator_accepts_custom_base_url_for_deepseek():
    # DeepSeek is OpenAI-compatible — same SDK, different base_url + model.
    t = OpenAITranslator(
        api_key="sk-deepseek",
        model="deepseek-chat",
        base_url="https://api.deepseek.com",
    )
    assert t._model == "deepseek-chat"
    assert "deepseek.com" in str(t._client.base_url)


@pytest.mark.asyncio
async def test_translator_returns_original_for_empty_input():
    t = OpenAITranslator(api_key="sk-test")
    # No mock needed -- empty input is short-circuited before any API call.
    assert await t.translate("") == ""
    assert await t.translate("   ") == "   "
