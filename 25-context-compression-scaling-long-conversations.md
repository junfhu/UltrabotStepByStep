# 课程 25：上下文压缩 — 扩展长对话

**目标：** 当对话历史接近模型的上下文窗口时，自动压缩对话历史，同时将关键信息保留在结构化摘要中。

**你将学到：**
- Token 估算启发式方法（字符数 ÷ 4）
- 头/尾保护：保持系统提示和最近消息不变
- 基于 LLM 的摘要生成，使用结构化输出模板
- 跨多次压缩的增量摘要堆叠
- 工具输出裁剪作为低成本的预压缩步骤

**新建文件：**
- `ultrabot/agent/context_compressor.py` — `ContextCompressor` 类

### 步骤 1：Token 估算与阈值

进行阈值检查时我们不需要精确的分词 — `字符数 / 4` 的启发式方法对英文文本的准确度在 ~10% 以内，且远比运行分词器快。

```python
# ultrabot/agent/context_compressor.py
"""基于 LLM 的长对话上下文压缩。

通过辅助客户端对对话中间部分进行摘要压缩，
同时保护头部（系统提示 + 首轮对话）和尾部（最近消息）。
"""

import logging
from typing import Optional

from ultrabot.agent.auxiliary import AuxiliaryClient

logger = logging.getLogger(__name__)

# 粗略估算：1 token ≈ 4 个字符（广泛使用的启发式方法）
_CHARS_PER_TOKEN = 4

# 当估算 token 数超过上下文限制的 80% 时触发压缩
_DEFAULT_THRESHOLD_RATIO = 0.80

# 摘要输入中每个工具结果保留的最大字符数
_MAX_TOOL_RESULT_CHARS = 3000

# 裁剪后的工具输出占位符
_PRUNED_TOOL_PLACEHOLDER = "[Tool output truncated to save context space]"

# 摘要前缀，让模型知道上下文已被压缩
SUMMARY_PREFIX = (
    "[CONTEXT COMPACTION] Earlier turns in this conversation were compacted "
    "to save context space. The summary below describes work that was "
    "already completed. Use it to continue without repeating work:"
)

# LLM 需要填写的结构化模板
_SUMMARY_TEMPLATE = """\
## Conversation Summary
**Goal:** [what the user is trying to accomplish]
**Progress:** [what has been done so far]
**Key Decisions:** [important choices made]
**Files Modified:** [files touched, if any]
**Next Steps:** [what remains to be done]"""

_SUMMARIZE_SYSTEM_PROMPT = f"""\
You are a context compressor. Given conversation turns, produce a structured \
summary using EXACTLY this template:

{_SUMMARY_TEMPLATE}

Be specific: include file paths, commands, error messages, and concrete values. \
Write only the summary — no preamble."""
```

### 步骤 2：ContextCompressor 类

压缩器保护头部（系统提示 + 首轮对话）和尾部（最近消息），仅压缩中间部分。

```python
class ContextCompressor:
    """当接近模型上下文限制时压缩对话上下文。

    Parameters
    ----------
    auxiliary : AuxiliaryClient
        用于生成摘要的 LLM 客户端（廉价模型）。
    threshold_ratio : float
        触发压缩的 context_limit 比例（0.80）。
    protect_head : int
        开头需要保护的消息数（默认 3：系统消息、第一条用户消息、第一条助手消息）。
    protect_tail : int
        末尾需要保护的最近消息数（默认 6）。
    max_summary_tokens : int
        摘要响应的最大 token 数（默认 1024）。
    """

    def __init__(
        self,
        auxiliary: AuxiliaryClient,
        threshold_ratio: float = _DEFAULT_THRESHOLD_RATIO,
        protect_head: int = 3,
        protect_tail: int = 6,
        max_summary_tokens: int = 1024,
    ) -> None:
        self.auxiliary = auxiliary
        self.threshold_ratio = threshold_ratio
        self.protect_head = max(1, protect_head)
        self.protect_tail = max(1, protect_tail)
        self.max_summary_tokens = max_summary_tokens
        self._previous_summary: Optional[str] = None  # 跨多次压缩堆叠
        self.compression_count: int = 0

    @staticmethod
    def estimate_tokens(messages: list[dict]) -> int:
        """粗略 token 估算：总字符数 / 4。"""
        if not messages:
            return 0
        total_chars = 0
        for msg in messages:
            content = msg.get("content") or ""
            total_chars += len(content) + 4   # 每条消息约 4 字符开销
            # 计入 tool_calls 参数
            for tc in msg.get("tool_calls", []):
                if isinstance(tc, dict):
                    args = tc.get("function", {}).get("arguments", "")
                    total_chars += len(args)
        return total_chars // _CHARS_PER_TOKEN

    def should_compress(self, messages: list[dict], context_limit: int) -> bool:
        """当估算 token 数超过阈值时返回 True。"""
        if not messages or context_limit <= 0:
            return False
        estimated = self.estimate_tokens(messages)
        threshold = int(context_limit * self.threshold_ratio)
        return estimated >= threshold
```

### 步骤 3：工具输出裁剪（低成本预处理）

在将消息发送给摘要 LLM 之前，我们先截断过大的工具输出。这是一个零成本优化 — 不需要 LLM 调用。

```python
    @staticmethod
    def prune_tool_output(
        messages: list[dict], max_chars: int = _MAX_TOOL_RESULT_CHARS,
    ) -> list[dict]:
        """截断过长的工具结果消息以节省 token。
        
        返回一个新列表 — 非工具消息原样传递。
        """
        if not messages:
            return []
        result: list[dict] = []
        for msg in messages:
            if msg.get("role") == "tool" and len(msg.get("content", "")) > max_chars:
                truncated = msg.copy()
                original = truncated["content"]
                truncated["content"] = (
                    original[:max_chars] + f"\n...{_PRUNED_TOOL_PLACEHOLDER}"
                )
                result.append(truncated)
            else:
                result.append(msg)
        return result
```

### 步骤 4：压缩方法

核心算法：将消息分为头部/中间/尾部，将中间部分序列化后交给摘要器，调用廉价 LLM，然后重新组装。

```python
    async def compress(self, messages: list[dict], max_tokens: int = 0) -> list[dict]:
        """通过摘要中间部分进行压缩。
        
        返回：头部 + [摘要消息] + 尾部
        """
        if not messages:
            return []
        n = len(messages)

        # 如果所有消息都在保护范围内，则无需压缩
        if n <= self.protect_head + self.protect_tail:
            return list(messages)

        head = messages[: self.protect_head]
        tail = messages[-self.protect_tail :]
        middle = messages[self.protect_head : n - self.protect_tail]

        if not middle:
            return list(messages)

        # 在摘要之前先裁剪中间部分的工具输出
        pruned_middle = self.prune_tool_output(middle)
        serialized = self._serialize_turns(pruned_middle)

        # 构建摘要提示 — 如果存在之前的摘要则合并
        if self._previous_summary:
            user_prompt = (
                f"Previous summary:\n{self._previous_summary}\n\n"
                f"New turns to incorporate:\n{serialized}\n\n"
                f"Update the summary using the structured template. "
                f"Preserve all relevant previous information."
            )
        else:
            user_prompt = f"Summarize these conversation turns:\n{serialized}"

        summary_messages = [
            {"role": "system", "content": _SUMMARIZE_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        summary_text = await self.auxiliary.complete(
            summary_messages,
            max_tokens=self.max_summary_tokens,
            temperature=0.3,
        )

        if not summary_text:
            summary_text = (
                f"(Summary generation failed. {len(middle)} messages were "
                f"removed to save context space.)"
            )

        # 为多轮压缩堆叠摘要
        self._previous_summary = summary_text
        self.compression_count += 1

        summary_message = {
            "role": "system",
            "content": f"{SUMMARY_PREFIX}\n\n{summary_text}",
        }

        return head + [summary_message] + tail
```

### 步骤 5：序列化辅助方法

将消息转换为带标签的文本格式，供摘要 LLM 解析。

```python
    @staticmethod
    def _serialize_turns(turns: list[dict]) -> str:
        """将消息转换为带标签的文本供摘要器使用。"""
        parts: list[str] = []
        for msg in turns:
            role = msg.get("role", "unknown").upper()
            content = msg.get("content") or ""

            # 截断过长的单条内容
            if len(content) > _MAX_TOOL_RESULT_CHARS:
                content = content[:2000] + "\n...[truncated]...\n" + content[-800:]

            if role == "TOOL":
                tool_id = msg.get("tool_call_id", "")
                parts.append(f"[TOOL RESULT {tool_id}]: {content}")
            elif role == "ASSISTANT":
                tool_calls = msg.get("tool_calls", [])
                if tool_calls:
                    tc_parts: list[str] = []
                    for tc in tool_calls:
                        if isinstance(tc, dict):
                            fn = tc.get("function", {})
                            name = fn.get("name", "?")
                            args = fn.get("arguments", "")
                            if len(args) > 500:
                                args = args[:400] + "..."
                            tc_parts.append(f"  {name}({args})")
                    content += "\n[Tool calls:\n" + "\n".join(tc_parts) + "\n]"
                parts.append(f"[ASSISTANT]: {content}")
            else:
                parts.append(f"[{role}]: {content}")

        return "\n\n".join(parts)
```

### 测试

```python
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
```

### 检查点

```bash
python -m pytest tests/test_context_compressor.py -v
```

预期结果：所有测试通过。压缩器能正确摘要对话中间部分，同时保护头部和尾部消息。

### 本课成果

一个基于 LLM 的上下文压缩器，使用结构化摘要模板（目标/进展/决策/文件/后续步骤）将长对话压缩为原始 token 开销的一小部分。它先裁剪工具输出（零成本），然后调用廉价模型进行实际摘要。摘要在多次压缩中累积堆叠，因此智能体永远不会丢失关键上下文。

---
