# Agent: 30课程开发指南
**从零开始构建一个生产级 AI 助手框架。**
本指南将带你从"向 LLM 问好"一步步走到一个完整的多提供者、多通道 AI 智能体，具备工具调用、记忆、安全防护和 Web 界面。每节课程都建立在上一节课的基础之上。每节课都包含可运行的代码和测试。  
本教程的主要思路来自于
- Nanobot (https://github.com/HKUDS/nanobot)
- Learn-Claude-Code (https://github.com/shareAI-lab/learn-claude-code/)

本课程设计由AI辅助下完成，因为课程自身也在不停修正，请参考 https://github.com/junfhu/UltrabotStepByStep，如果您觉得对您有帮助，请帮助点亮一颗星。  



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

> **pytest 配置**：本课的异步测试使用 `@pytest.mark.asyncio`，需要在 `pyproject.toml` 中添加：
> ```toml
> [tool.pytest.ini_options]
> asyncio_mode = "auto"
> ```

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

## 本课使用的 Python 知识

### `logging` 标准库日志模块

`logging` 是 Python 内置的日志框架，通过 `logging.getLogger(__name__)` 为当前模块创建独立的日志记录器，方便按模块过滤日志。

```python
import logging

logger = logging.getLogger(__name__)
logger.info("压缩完成，移除了 %d 条消息", count)
logger.warning("摘要生成失败，使用兜底消息")
```

**为什么在本课中使用：** 本课使用标准库 `logging` 而非 `loguru`，展示了另一种日志方案。`getLogger(__name__)` 让日志自动带上模块名，方便在大型项目中定位问题来源。

### `typing.Optional` 可选类型

`Optional[X]` 等价于 `X | None`，表示一个值可以是类型 `X` 或者 `None`。在较早版本的 Python 中常用。

```python
from typing import Optional

class ContextCompressor:
    def __init__(self) -> None:
        self._previous_summary: Optional[str] = None  # 字符串或 None
```

**为什么在本课中使用：** `_previous_summary` 初始为 `None`（尚未压缩过），压缩后存储摘要字符串。`Optional[str]` 清楚地表达了这种"有或无"的语义。

### `class` 与 `__init__` 面向对象编程

Python 的类通过 `class` 关键字定义，`__init__` 是构造方法，在创建实例时自动调用。`self` 指向当前实例。

```python
class ContextCompressor:
    def __init__(self, auxiliary, threshold_ratio: float = 0.80) -> None:
        self.auxiliary = auxiliary
        self.threshold_ratio = threshold_ratio
        self.compression_count: int = 0
```

**为什么在本课中使用：** `ContextCompressor` 类封装了压缩逻辑的所有状态（辅助客户端、阈值、保护头尾的消息数、摘要历史），通过方法（`should_compress`、`compress`）提供操作接口。

### `@staticmethod` 静态方法

`@staticmethod` 定义不需要访问实例（`self`）或类（`cls`）的方法，是逻辑上属于类但不依赖实例状态的纯函数。

```python
class ContextCompressor:
    @staticmethod
    def estimate_tokens(messages: list[dict]) -> int:
        total_chars = sum(len(m.get("content", "")) for m in messages)
        return total_chars // 4
```

**为什么在本课中使用：** `estimate_tokens()` 和 `prune_tool_output()` 是通用工具函数——它们只依赖输入参数，不需要访问压缩器的实例状态，定义为静态方法更清晰合理。

### `async / await` 异步编程

`async def` 定义协程函数，`await` 用于等待异步操作（如 LLM API 调用）完成。在等待期间，事件循环可以执行其他任务。

```python
async def compress(self, messages: list[dict]) -> list[dict]:
    summary_text = await self.auxiliary.complete(
        summary_messages,
        max_tokens=self.max_summary_tokens,
        temperature=0.3,
    )
    return head + [summary_message] + tail
```

**为什么在本课中使用：** 摘要生成需要调用 LLM API，这是一个耗时的 I/O 操作。`async/await` 让程序在等待 API 响应时不被阻塞，可以同时处理其他请求。

### `isinstance()` 类型检查

`isinstance(obj, type)` 检查对象是否是指定类型的实例，比直接用 `type()` 更灵活（支持继承关系）。

```python
for tc in msg.get("tool_calls", []):
    if isinstance(tc, dict):
        fn = tc.get("function", {})
        name = fn.get("name", "?")
```

**为什么在本课中使用：** `tool_calls` 字段的元素理论上应该是字典，但实际数据可能不规范。用 `isinstance(tc, dict)` 做防御性检查，只处理合法的工具调用数据。

### `dict.get()` 字典安全取值

`dict.get(key, default)` 在键不存在时返回默认值，而不是抛出 `KeyError`。

```python
content = msg.get("content") or ""  # 键不存在或值为 None 时用空字符串
role = msg.get("role", "unknown")   # 键不存在时用 "unknown"
```

**为什么在本课中使用：** 消息字典的结构不固定——有些消息可能没有 `content` 字段（如纯工具调用），有些可能没有 `tool_calls`。`.get()` 让代码对缺失字段免疫。

### `dict.copy()` 字典浅拷贝

`dict.copy()` 创建字典的浅拷贝——新字典与原字典有相同的键值对，但修改新字典不影响原字典。

```python
truncated = msg.copy()  # 不修改原始消息
truncated["content"] = original[:max_chars] + "...[truncated]"
result.append(truncated)
```

**为什么在本课中使用：** `prune_tool_output()` 截断过长的工具输出时，必须先复制消息字典再修改，否则会意外修改原始消息列表中的数据。

### `list()` 列表复制和列表切片

`list(original)` 创建列表的浅拷贝。列表切片 `lst[a:b]` 返回一个新的子列表。负数索引 `lst[-n:]` 取最后 n 个元素。

```python
messages = [msg1, msg2, msg3, ..., msg20]

head = messages[:3]       # 前 3 条（系统提示 + 首轮对话）
tail = messages[-6:]      # 最后 6 条（最近的消息）
middle = messages[3:-6]   # 中间部分（待压缩）
```

**为什么在本课中使用：** 压缩算法的核心是"头尾保护"——用切片把消息分为三段：头部（保护）、中间（压缩为摘要）、尾部（保护）。

### `f-string` 和多行字符串常量

f-string 用于字符串格式化，三引号多行字符串用于定义长文本模板。

```python
SUMMARY_PREFIX = (
    "[CONTEXT COMPACTION] Earlier turns in this conversation were compacted "
    "to save context space."
)

_SUMMARY_TEMPLATE = """\
## Conversation Summary
**Goal:** [what the user is trying to accomplish]
**Progress:** [what has been done so far]
**Key Decisions:** [important choices made]"""
```

**为什么在本课中使用：** 摘要提示模板和前缀是长字符串常量，用多行字符串和字符串拼接（相邻字符串自动拼接）保持代码整洁可读。

### `max()` 内置函数

`max(a, b)` 返回两个值中的较大者，常用于设置下限——确保某个值不低于最小要求。

```python
self.protect_head = max(1, protect_head)  # 至少保护 1 条消息
self.protect_tail = max(1, protect_tail)  # 至少保护 1 条消息
```

**为什么在本课中使用：** 即使调用者传入 `protect_head=0`，也至少保护 1 条消息（通常是系统提示），`max(1, n)` 强制了这个下限。

### `int()` 类型转换

`int()` 将浮点数或字符串转换为整数。对浮点数执行截断（去掉小数部分），不是四舍五入。

```python
threshold = int(context_limit * self.threshold_ratio)
# 例如 int(128000 * 0.80) = 102400
```

**为什么在本课中使用：** token 阈值计算结果是浮点数（如 `128000 × 0.80 = 102400.0`），需要转为整数与估算的 token 数进行比较。

### `pytest.mark.asyncio` 异步测试

`@pytest.mark.asyncio` 标记一个测试函数为异步测试——pytest 会自动创建事件循环来运行它。需要安装 `pytest-asyncio` 插件。

```python
import pytest

@pytest.mark.asyncio
async def test_compress():
    result = await compressor.compress(messages)
    assert len(result) < len(messages)
```

**为什么在本课中使用：** `compress()` 是异步方法（需要 `await` 调用 LLM API），测试也必须是异步的才能正确测试。

### `unittest.mock.AsyncMock` 和 `MagicMock`

`AsyncMock` 模拟异步函数/方法，`MagicMock` 模拟普通对象。两者都可以配置返回值，用于隔离测试。

```python
from unittest.mock import AsyncMock, MagicMock

aux = AsyncMock()
aux.complete = AsyncMock(return_value="## Summary\n**Goal:** test")

comp = ContextCompressor(auxiliary=aux)
result = await comp.compress(messages)

aux.complete.assert_called_once()  # 验证 LLM 被调用了一次
```

**为什么在本课中使用：** 测试压缩器时不可能真正调用 LLM API（成本高、不可控）。用 `AsyncMock` 模拟辅助客户端的 `complete()` 方法，返回预设的摘要文本。

### `any()` 内置函数

`any(iterable)` 只要可迭代对象中有一个元素为真就返回 `True`。常与生成器表达式配合使用。

```python
# 检查结果中是否有任何消息包含摘要前缀
has_summary = any(SUMMARY_PREFIX in m.get("content", "") for m in result)
assert has_summary
```

**为什么在本课中使用：** 测试中需要验证压缩后的消息列表中是否包含摘要消息。`any()` 配合生成器表达式简洁地完成了这个检查。

### 列表拼接 `+`

Python 中两个列表可以用 `+` 运算符拼接成一个新列表。

```python
head = [msg1, msg2]
summary = [summary_msg]
tail = [msg18, msg19, msg20]

result = head + summary + tail  # 拼接为新列表
```

**为什么在本课中使用：** 压缩完成后，需要将头部消息、摘要消息和尾部消息拼接为最终的压缩结果。`head + [summary_message] + tail` 简洁地完成了组装。
