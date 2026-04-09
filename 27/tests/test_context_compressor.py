# tests/test_context_compressor.py
"""上下文压缩系统的测试。"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from ultrabot.agent.context_compressor import (
    ContextCompressor, SUMMARY_PREFIX, _PRUNED_TOOL_PLACEHOLDER,
)


def _make_messages(n: int, content_size: int = 100) -> list[dict]:
    """创建 n 条交替的用户/助手消息。"""
    msgs = [{"role": "system", "content": "You are helpful."}]
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append({"role": role, "content": f"Message {i}: " + "x" * content_size})
    return msgs


class TestTokenEstimation:
    def test_empty(self):
        assert ContextCompressor.estimate_tokens([]) == 0

    def test_simple(self):
        msgs = [{"role": "user", "content": "Hello world"}]
        # (11 字符 + 4 开销) / 4 = 3
        assert ContextCompressor.estimate_tokens(msgs) == 3

    def test_with_tool_calls(self):
        msgs = [{"role": "assistant", "content": "ok",
                 "tool_calls": [{"function": {"arguments": "x" * 100}}]}]
        tokens = ContextCompressor.estimate_tokens(msgs)
        assert tokens > 25  # (2 + 4 + 100) / 4 = 26


class TestShouldCompress:
    def test_below_threshold(self):
        aux = MagicMock()
        comp = ContextCompressor(auxiliary=aux)
        msgs = _make_messages(5, 10)
        assert comp.should_compress(msgs, context_limit=100_000) is False

    def test_above_threshold(self):
        aux = MagicMock()
        comp = ContextCompressor(auxiliary=aux, threshold_ratio=0.01)
        msgs = _make_messages(5, 100)
        assert comp.should_compress(msgs, context_limit=10) is True


class TestPruneToolOutput:
    def test_short_tool_output_unchanged(self):
        msgs = [{"role": "tool", "content": "short"}]
        result = ContextCompressor.prune_tool_output(msgs)
        assert result[0]["content"] == "short"

    def test_long_tool_output_truncated(self):
        msgs = [{"role": "tool", "content": "x" * 5000}]
        result = ContextCompressor.prune_tool_output(msgs, max_chars=100)
        assert len(result[0]["content"]) < 5000
        assert _PRUNED_TOOL_PLACEHOLDER in result[0]["content"]


class TestCompress:
    @pytest.mark.asyncio
    async def test_compress_produces_summary(self):
        aux = AsyncMock()
        aux.complete = AsyncMock(return_value="## Conversation Summary\n**Goal:** test")

        comp = ContextCompressor(auxiliary=aux, protect_head=2, protect_tail=2)
        msgs = _make_messages(20, 50)

        result = await comp.compress(msgs)

        # 应比原始消息更短
        assert len(result) < len(msgs)
        # 应包含摘要前缀
        assert any(SUMMARY_PREFIX in m.get("content", "") for m in result)
        # 压缩计数已递增
        assert comp.compression_count == 1

    @pytest.mark.asyncio
    async def test_compress_too_few_messages_returns_unchanged(self):
        aux = AsyncMock()
        comp = ContextCompressor(auxiliary=aux, protect_head=3, protect_tail=3)
        msgs = _make_messages(4, 50)

        result = await comp.compress(msgs)
        assert len(result) == len(msgs)

    @pytest.mark.asyncio
    async def test_fallback_on_llm_failure(self):
        aux = AsyncMock()
        aux.complete = AsyncMock(return_value="")  # LLM 失败

        comp = ContextCompressor(auxiliary=aux, protect_head=2, protect_tail=2)
        msgs = _make_messages(20, 50)

        result = await comp.compress(msgs)
        # 仍然应该压缩，只是使用了兜底消息
        assert len(result) < len(msgs)
