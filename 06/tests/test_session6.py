# tests/test_session6.py
"""课程 6 的测试 -- 提供者抽象。"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from ultrabot.providers.base import (
    LLMProvider, LLMResponse, GenerationSettings, ToolCallRequest,
)
from ultrabot.providers.registry import find_by_name, find_by_keyword, PROVIDERS


def test_llm_response_dataclass():
    """LLMResponse 按预期工作。"""
    resp = LLMResponse(content="Hello")
    assert resp.content == "Hello"
    assert not resp.has_tool_calls

    resp2 = LLMResponse(
        tool_calls=[ToolCallRequest(id="1", name="test", arguments={})]
    )
    assert resp2.has_tool_calls


def test_generation_settings_defaults():
    """GenerationSettings 有合理的默认值。"""
    gs = GenerationSettings()
    assert gs.temperature == 0.7
    assert gs.max_tokens == 4096


def test_tool_call_serialization():
    """ToolCallRequest 序列化为 OpenAI 格式。"""
    tc = ToolCallRequest(id="call_123", name="read_file", arguments={"path": "."})
    openai_fmt = tc.to_openai_tool_call()

    assert openai_fmt["id"] == "call_123"
    assert openai_fmt["type"] == "function"
    assert openai_fmt["function"]["name"] == "read_file"


def test_transient_error_detection():
    """_is_transient_error 检测可重试错误。"""
    # 速率限制（状态码 429）
    exc_429 = Exception("rate limited")
    exc_429.status_code = 429  # type: ignore
    assert LLMProvider._is_transient_error(exc_429)

    # 超时
    class TimeoutError_(Exception):
        pass
    assert LLMProvider._is_transient_error(TimeoutError_("timed out"))

    # 非瞬态错误
    assert not LLMProvider._is_transient_error(ValueError("bad input"))


def test_find_by_name():
    """find_by_name 按名称查找提供者（不区分大小写）。"""
    spec = find_by_name("openai_compatible")
    assert spec is not None
    assert spec.name == "openai_compatible"

    assert find_by_name("nonexistent") is None


def test_find_by_keyword():
    """find_by_keyword 按关键词元组匹配。"""
    spec = find_by_keyword("gpt")
    assert spec is not None
    assert spec.name == "openai_compatible"

    spec = find_by_keyword("claude")
    assert spec is not None
    assert spec.name == "anthropic"


def test_all_providers_have_required_fields():
    """每个已注册的提供者都有 name 和 backend。"""
    for spec in PROVIDERS:
        assert spec.name
        assert spec.backend in ("openai_compat", "anthropic")
