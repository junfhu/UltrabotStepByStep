# Ultrabot：30 课程开发指南
**从零开始构建一个生产级 AI 助手框架。**
本指南将带你从"向 LLM 问好"一步步走到一个完整的多提供者、多通道 AI 智能体，具备工具调用、记忆、安全防护和 Web 界面。每节课程都建立在上一节课的基础之上。每节课都包含可运行的代码和测试。  
本教程的主要思路来自于
- Nanobot (https://github.com/HKUDS/nanobot)
- Learn-Claude-Code (https://github.com/shareAI-lab/learn-claude-code/)

本课程设计由AI辅助下完成，因为课程自身也在不停修正，请参考 https://github.com/junfhu/UltrabotStepByStep，如果您觉得对您有帮助，请帮助点亮一颗星。  
本课程中使用的大模型提供商是火山引擎Code Plan，如果正好你也需要，可以使用我的邀请码获取9折优惠 https://volcengine.com/L/_01BJCkKdMc/  邀请码：HHCDB4J4）  



# 课程 7：Anthropic 提供者 -- 添加 Claude

**目标：** 添加原生 Anthropic（Claude）支持，了解不同 LLM API 之间的差异。

**你将学到：**
- Anthropic 的消息格式与 OpenAI 的区别
- 系统提示词提取（Anthropic 将其放在消息数组之外）
- 工具使用格式转换（OpenAI functions -> Anthropic tool_use 块）
- 带有内容块组装的流式输出
- 用于标准化不同 API 的适配器模式

**特别说明：**
- 因为国内访问anthropic不方便，我们仍旧使用火山引擎的Anthropic兼容API，以及minimax-m2.5模型

**新建文件：**
- `ultrabot/providers/anthropic_provider.py` -- 原生 Anthropic 提供者

**前置条件：项目结构**

到这一课，你的项目应该具有标准的 Python 包布局。项目根目录下有一个 `ultrabot/` 子目录作为 Python 包，源代码都在其中：

```
ultrabot/                  # 项目根目录
├── pyproject.toml
├── ultrabot/              # Python 包
│   ├── __init__.py
│   ├── agent.py
│   ├── chat.py
│   ├── main.py
│   ├── config/
│   │   ├── __init__.py
│   │   ├── schema.py
│   │   ├── loader.py
│   │   └── paths.py
│   ├── providers/
│   │   ├── __init__.py    # 课程 6 创建
│   │   ├── base.py
│   │   ├── openai_compat.py
│   │   └── registry.py
│   └── tools/
│       ├── __init__.py
│       ├── base.py
│       ├── builtin.py
│       └── toolsets.py
└── tests/
    └── test_session7.py   # 本课新增
```

> **重要：** 如果你的源文件（`providers/`、`config/` 等）直接放在项目根目录而不是 `ultrabot/` 子目录中，`from ultrabot.xxx import ...` 的导入会失败。请确保项目根目录下有 `ultrabot/` 子目录，并且其中包含 `__init__.py`。

### 步骤 1：安装 Anthropic SDK

```bash
pip install anthropic
```

更新 `pyproject.toml`：

```toml
[project]
name = "ultrabot"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "openai>=1.0",
    "anthropic>=0.30",
    "pydantic>=2.0",
    "pydantic-settings>=2.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

然后以可编辑模式安装包，使 `from ultrabot.xxx` 导入在项目目录内生效：

```bash
pip install -e .
```

### 步骤 2：理解 API 差异

| 特性              | OpenAI                          | Anthropic                        |
|-------------------|---------------------------------|----------------------------------|
| 系统提示词         | `{"role": "system", ...}` 消息   | 单独的 `system` 参数              |
| 工具定义           | `{"type": "function", ...}`     | `{"name": ..., "input_schema"}` |
| 工具结果           | `{"role": "tool", ...}` 消息     | `{"role": "user", "content": [{"type": "tool_result", ...}]}` |
| 工具调用格式       | `function.arguments`（JSON 字符串）| `input`（字典）                   |
| 消息顺序           | 灵活                             | 严格的 user/assistant 交替        |

`AnthropicProvider` 会透明地处理所有这些转换。

### 步骤 3：构建 Anthropic 提供者

```python
# ultrabot/providers/anthropic_provider.py
"""Anthropic（Claude）提供者。

将内部 OpenAI 风格的消息格式与 Anthropic Messages API 互相转换，
包括系统提示词、工具使用块和流式输出。

取自 ultrabot/providers/anthropic_provider.py。
"""
from __future__ import annotations

import json
import uuid
from copy import deepcopy
from typing import Any, Callable, Coroutine

from ultrabot.providers.base import (
    GenerationSettings, LLMProvider, LLMResponse, ToolCallRequest,
)


class AnthropicProvider(LLMProvider):
    """Anthropic Messages API 的提供者。

    取自 ultrabot/providers/anthropic_provider.py 第 26-528 行。
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        generation: GenerationSettings | None = None,
        default_model: str = "minimax-m2.5",
    ) -> None:
        super().__init__(api_key=api_key, api_base=api_base, generation=generation)
        self._default_model = default_model
        self._client: Any | None = None

    @property
    def client(self) -> Any:
        """延迟创建 AsyncAnthropic 客户端。"""
        if self._client is None:
            import anthropic
            kwargs: dict[str, Any] = {"api_key": self.api_key, "max_retries": 0}
            if self.api_base:
                kwargs["base_url"] = self.api_base
            self._client = anthropic.AsyncAnthropic(**kwargs)
        return self._client

    # -- 非流式聊天 --

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        model = model or self._default_model

        # 关键步骤：将 OpenAI 消息转换为 Anthropic 格式
        system_text, anthropic_msgs = self._convert_messages(messages)

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": anthropic_msgs,
            "max_tokens": max_tokens or self.generation.max_tokens,
            "temperature": temperature or self.generation.temperature,
        }

        # Anthropic 将系统提示词作为单独的参数
        if system_text:
            kwargs["system"] = system_text

        if tools:
            kwargs["tools"] = self._convert_tools(tools)

        response = await self.client.messages.create(**kwargs)
        return self._map_response(response)

    # -- 流式聊天 --

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        on_content_delta: Callable[[str], Coroutine[Any, Any, None]] | None = None,
    ) -> LLMResponse:
        """使用 Anthropic 基于事件的协议进行流式响应。

        取自 ultrabot/providers/anthropic_provider.py 第 128-248 行。
        Anthropic 流式传输 content_block_start/delta/stop 事件，
        而不是像 OpenAI 那样的简单 delta chunk。
        """
        model = model or self._default_model
        system_text, anthropic_msgs = self._convert_messages(messages)

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": anthropic_msgs,
            "max_tokens": max_tokens or self.generation.max_tokens,
            "temperature": temperature or self.generation.temperature,
        }
        if system_text:
            kwargs["system"] = system_text
        if tools:
            kwargs["tools"] = self._convert_tools(tools)

        content_parts: list[str] = []
        tool_calls: list[ToolCallRequest] = []
        finish_reason: str | None = None

        # 追踪当前正在流式传输的内容块
        current_block_type: str | None = None
        current_block_id: str | None = None
        current_block_name: str | None = None
        current_block_text: list[str] = []

        async with self.client.messages.stream(**kwargs) as stream:
            async for event in stream:
                event_type = getattr(event, "type", None)

                if event_type == "content_block_start":
                    block = event.content_block
                    current_block_type = block.type
                    current_block_text = []
                    if block.type == "tool_use":
                        current_block_id = block.id
                        current_block_name = block.name

                elif event_type == "content_block_delta":
                    delta = event.delta
                    delta_type = getattr(delta, "type", None)
                    if delta_type == "text_delta":
                        content_parts.append(delta.text)
                        if on_content_delta:
                            await on_content_delta(delta.text)
                    elif delta_type == "input_json_delta":
                        # 工具调用参数以增量方式到达
                        current_block_text.append(delta.partial_json)

                elif event_type == "content_block_stop":
                    if current_block_type == "tool_use":
                        # 组装完整的工具调用
                        raw_json = "".join(current_block_text)
                        try:
                            args = json.loads(raw_json) if raw_json else {}
                        except json.JSONDecodeError:
                            args = {"_raw": raw_json}
                        tool_calls.append(ToolCallRequest(
                            id=current_block_id or str(uuid.uuid4()),
                            name=current_block_name or "",
                            arguments=args,
                        ))
                    current_block_type = None
                    current_block_text = []

                elif event_type == "message_delta":
                    sr = getattr(getattr(event, "delta", None), "stop_reason", None)
                    if sr:
                        finish_reason = sr

        return LLMResponse(
            content="".join(content_parts) or None,
            tool_calls=tool_calls,
            finish_reason=self._map_stop_reason(finish_reason),
        )

    # ----------------------------------------------------------------
    # 消息转换（最复杂的部分！）
    # ----------------------------------------------------------------

    @staticmethod
    def _convert_messages(
        messages: list[dict[str, Any]],
    ) -> tuple[str, list[dict[str, Any]]]:
        """分离系统消息并将所有内容转换为 Anthropic 格式。

        取自 ultrabot/providers/anthropic_provider.py 第 252-312 行。

        关键转换：
        - system 消息 -> 提取为单独的 system_text
        - tool 结果 -> 包装在带有 tool_result 块的 user 消息中
        - assistant tool_calls -> 转换为 tool_use 块
        - 连续相同角色的消息 -> 合并（Anthropic 要求交替出现）
        """
        system_parts: list[str] = []
        converted: list[dict[str, Any]] = []

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content")

            # 系统消息被提取出来
            if role == "system":
                if isinstance(content, str):
                    system_parts.append(content)
                continue

            # 工具结果变成带有 tool_result 块的 user 消息
            if role == "tool":
                converted.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg.get("tool_call_id", ""),
                        "content": content if isinstance(content, str) else json.dumps(content),
                    }],
                })
                continue

            # 助手消息：将 tool_calls 转换为 tool_use 块
            if role == "assistant":
                blocks: list[dict[str, Any]] = []
                if content and isinstance(content, str):
                    blocks.append({"type": "text", "text": content})
                tool_calls = msg.get("tool_calls")
                if tool_calls:
                    for tc in tool_calls:
                        func = tc.get("function", {})
                        raw_args = func.get("arguments", "{}")
                        try:
                            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                        except json.JSONDecodeError:
                            args = {"_raw": raw_args}
                        blocks.append({
                            "type": "tool_use",
                            "id": tc.get("id", str(uuid.uuid4())),
                            "name": func.get("name", ""),
                            "input": args,
                        })
                converted.append({
                    "role": "assistant",
                    "content": blocks or [{"type": "text", "text": " "}],
                })
                continue

            # 用户消息
            converted.append({
                "role": "user",
                "content": content or " ",
            })

        # 合并连续相同角色的消息（Anthropic 的要求）
        converted = AnthropicProvider._merge_consecutive_roles(converted)

        return "\n\n".join(system_parts), converted

    @staticmethod
    def _merge_consecutive_roles(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """合并连续相同角色的消息。

        取自 ultrabot/providers/anthropic_provider.py 第 391-411 行。
        Anthropic 要求严格的 user/assistant 交替。
        """
        if not messages:
            return messages
        merged = [deepcopy(messages[0])]
        for msg in messages[1:]:
            if msg["role"] == merged[-1]["role"]:
                prev = merged[-1]["content"]
                new = msg["content"]
                # 标准化为块列表
                if isinstance(prev, str):
                    prev = [{"type": "text", "text": prev}]
                if isinstance(new, str):
                    new = [{"type": "text", "text": new}]
                merged[-1]["content"] = prev + new
            else:
                merged.append(deepcopy(msg))
        return merged

    # ----------------------------------------------------------------
    # 工具转换
    # ----------------------------------------------------------------

    @staticmethod
    def _convert_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """将 OpenAI 工具定义转换为 Anthropic 格式。

        取自 ultrabot/providers/anthropic_provider.py 第 415-434 行。

        OpenAI: {"type": "function", "function": {"name": ..., "parameters": ...}}
        Anthropic: {"name": ..., "description": ..., "input_schema": ...}
        """
        anthropic_tools = []
        for tool in tools:
            if tool.get("type") == "function":
                func = tool["function"]
                anthropic_tools.append({
                    "name": func["name"],
                    "description": func.get("description", ""),
                    "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
                })
            else:
                anthropic_tools.append(tool)
        return anthropic_tools

    # ----------------------------------------------------------------
    # 响应映射
    # ----------------------------------------------------------------

    @staticmethod
    def _map_response(response: Any) -> LLMResponse:
        """将 Anthropic Message 转换为 LLMResponse。

        取自 ultrabot/providers/anthropic_provider.py 第 459-490 行。
        """
        content_parts: list[str] = []
        tool_calls: list[ToolCallRequest] = []

        for block in response.content:
            if block.type == "text":
                content_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCallRequest(
                    id=block.id,
                    name=block.name,
                    arguments=block.input if isinstance(block.input, dict) else {},
                ))

        usage = {}
        if response.usage:
            usage = {
                "prompt_tokens": getattr(response.usage, "input_tokens", 0),
                "completion_tokens": getattr(response.usage, "output_tokens", 0),
                "total_tokens": (
                    getattr(response.usage, "input_tokens", 0)
                    + getattr(response.usage, "output_tokens", 0)
                ),
            }

        return LLMResponse(
            content="".join(content_parts) or None,
            tool_calls=tool_calls,
            finish_reason=AnthropicProvider._map_stop_reason(response.stop_reason),
            usage=usage,
        )

    @staticmethod
    def _map_stop_reason(stop_reason: str | None) -> str | None:
        """将 Anthropic 停止原因映射为 OpenAI 风格的完成原因。"""
        mapping = {
            "end_turn": "stop",
            "tool_use": "tool_calls",
            "max_tokens": "length",
        }
        return mapping.get(stop_reason or "", stop_reason)
```

### 测试

```python
# tests/test_session7.py
"""课程 7 的测试 -- Anthropic 提供者。"""
import json
import pytest
from ultrabot.providers.anthropic_provider import AnthropicProvider


def test_convert_messages_extracts_system():
    """系统消息被提取为单独的系统文本。"""
    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hello"},
    ]
    system_text, converted = AnthropicProvider._convert_messages(messages)

    assert system_text == "You are helpful."
    assert len(converted) == 1
    assert converted[0]["role"] == "user"


def test_convert_messages_tool_result():
    """OpenAI 工具结果变成 Anthropic tool_result 块。"""
    messages = [
        {"role": "user", "content": "List files"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "list_directory", "arguments": '{"path": "."}'}}
        ]},
        {"role": "tool", "tool_call_id": "call_1", "content": "file1.py\nfile2.py"},
    ]
    _, converted = AnthropicProvider._convert_messages(messages)

    # 工具结果应该是一个带有 tool_result 块的 user 消息
    tool_msg = converted[-1]
    assert tool_msg["role"] == "user"
    assert tool_msg["content"][0]["type"] == "tool_result"
    assert tool_msg["content"][0]["tool_use_id"] == "call_1"


def test_convert_tools_format():
    """OpenAI 工具定义被转换为 Anthropic 格式。"""
    openai_tools = [{
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    }]

    anthropic_tools = AnthropicProvider._convert_tools(openai_tools)
    assert len(anthropic_tools) == 1
    assert anthropic_tools[0]["name"] == "read_file"
    assert "input_schema" in anthropic_tools[0]
    assert "type" not in anthropic_tools[0]  # 没有 "type": "function"


def test_merge_consecutive_roles():
    """连续相同角色的消息被合并。"""
    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "user", "content": "World"},  # 连续的 user
    ]
    merged = AnthropicProvider._merge_consecutive_roles(messages)

    assert len(merged) == 1
    assert merged[0]["role"] == "user"
    # 内容应该被合并为块列表
    assert isinstance(merged[0]["content"], list)
    assert len(merged[0]["content"]) == 2


def test_map_stop_reason():
    """Anthropic 停止原因映射为 OpenAI 风格的原因。"""
    assert AnthropicProvider._map_stop_reason("end_turn") == "stop"
    assert AnthropicProvider._map_stop_reason("tool_use") == "tool_calls"
    assert AnthropicProvider._map_stop_reason("max_tokens") == "length"
    assert AnthropicProvider._map_stop_reason(None) is None


def test_assistant_message_with_tool_calls():
    """带有 tool_calls 的助手消息被转换为 tool_use 块。"""
    messages = [
        {"role": "assistant", "content": "Let me check.", "tool_calls": [
            {"id": "tc_1", "type": "function",
             "function": {"name": "read_file", "arguments": '{"path": "test.py"}'}},
        ]},
    ]
    _, converted = AnthropicProvider._convert_messages(messages)

    blocks = converted[0]["content"]
    assert blocks[0]["type"] == "text"
    assert blocks[0]["text"] == "Let me check."
    assert blocks[1]["type"] == "tool_use"
    assert blocks[1]["name"] == "read_file"
    assert blocks[1]["input"] == {"path": "test.py"}
```

运行测试：

```bash
pytest tests/test_session7.py -v
```

### 检查点

```python
import asyncio
from ultrabot.providers.anthropic_provider import AnthropicProvider

# 创建 Anthropic 提供者（这里使用火山引擎的 Anthropic 兼容端点）
provider = AnthropicProvider(
    api_key="sk-...",
    api_base="https://ark.cn-beijing.volces.com/api/coding",
    default_model="minimax-m2.5",
)

# 与 OpenAICompatProvider 接口完全相同！
response = asyncio.run(provider.chat(
    messages=[
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "What is Python?"},
    ],
))

print(response.content)
```

只需修改一行代码即可在 OpenAI 兼容提供者和 Anthropic 提供者之间切换：

```python
# OpenAI 兼容
provider = OpenAICompatProvider(
    api_key="sk-...",
    api_base="https://ark.cn-beijing.volces.com/api/coding/v3",
    default_model="minimax-m2.5",
)

# Anthropic -- Agent 接口完全相同
provider = AnthropicProvider(
    api_key="sk-...",
    api_base="https://ark.cn-beijing.volces.com/api/coding",
    default_model="minimax-m2.5",
)
```

### 本课成果

一个原生 Anthropic 提供者，处理 OpenAI 和 Anthropic API 之间的所有格式差异。适配器模式意味着我们的 Agent 类不关心它在和哪个 LLM 对话 -- 两个提供者都返回相同的 `LLMResponse` 格式。这直接对应 `ultrabot/providers/anthropic_provider.py`。

---

## 本课使用的 Python 知识

### `from __future__ import annotations`（延迟注解求值）

让 Python 把所有类型注解当作字符串处理，不在定义时立即求值。这样就可以使用 `str | None` 等 Python 3.10+ 语法，同时兼容更早的版本。

```python
from __future__ import annotations

def greet(name: str | None = None) -> str:  # 不加 future annotations，3.9 会报错
    return f"Hello, {name or 'World'}"
```

**本课为什么用它：** Anthropic 提供者中到处使用 `str | None`、`list[dict[str, Any]]` 等现代注解，这行保证向后兼容。

### `uuid.uuid4()`（生成唯一标识符）

`uuid4()` 生成一个随机的 UUID（通用唯一标识符），每次调用都不一样，冲突概率极低。

```python
import uuid

unique_id = str(uuid.uuid4())
print(unique_id)  # 例如 '550e8400-e29b-41d4-a716-446655440000'
```

**本课为什么用它：** Anthropic 流式响应中的工具调用块可能缺少 `id` 字段。用 `str(uuid.uuid4())` 生成一个备用 ID，确保每个工具调用都有唯一标识符，不会和其他调用混淆。

### `copy.deepcopy()`（深拷贝）

`deepcopy()` 递归地复制一个对象及其所有子对象，修改副本不会影响原始数据。相比之下，`=` 赋值只是引用，`list()` 或 `dict()` 只做浅拷贝。

```python
from copy import deepcopy

original = [{"a": [1, 2]}, {"b": [3, 4]}]
shallow = list(original)     # 浅拷贝：内层字典仍是同一个对象
deep = deepcopy(original)    # 深拷贝：完全独立

deep[0]["a"].append(99)
print(original[0]["a"])      # [1, 2] — 不受影响
shallow[0]["a"].append(99)
print(original[0]["a"])      # [1, 2, 99] — 被影响了！
```

**本课为什么用它：** `_merge_consecutive_roles()` 合并相同角色的连续消息时用 `deepcopy` 复制消息字典，确保合并操作不会修改传入的原始消息列表。

### `async with`（异步上下文管理器）

`async with` 是 `with` 的异步版本，用于需要异步初始化/清理的资源（如网络连接、流式传输）。

```python
async with client.stream() as stream:
    async for event in stream:
        process(event)
# 退出 async with 块时，流会被自动清理
```

**本课为什么用它：** `chat_stream()` 中用 `async with self.client.messages.stream(**kwargs) as stream` 开启 Anthropic 的流式连接。`async with` 确保无论是否发生异常，流连接都会被正确关闭，不会泄漏资源。

### `async for`（异步迭代）

`async for` 遍历异步迭代器，每次取下一个元素时可能需要等待（如等待网络数据到达）。

```python
async with client.messages.stream(**kwargs) as stream:
    async for event in stream:
        if event.type == "content_block_delta":
            print(event.delta.text, end="")
```

**本课为什么用它：** Anthropic 的流式响应逐事件到达（`content_block_start`、`content_block_delta`、`content_block_stop`），用 `async for event in stream` 逐个处理，实现打字机效果的实时输出。

### `isinstance()`（类型检查）

`isinstance(obj, type)` 检查对象是否是某个类型（或其子类）的实例。比直接用 `type(obj) == SomeType` 更安全，因为它支持继承。

```python
value = "hello"
if isinstance(value, str):
    print("是字符串")
elif isinstance(value, list):
    print("是列表")
```

**本课为什么用它：** 消息格式转换时需要判断 `content` 是字符串还是列表（块数组），`arguments` 是 JSON 字符串还是字典。`isinstance` 让代码能安全地处理两种输入格式。

### `@staticmethod`（静态方法）

不依赖实例状态（`self`）的方法，用 `@staticmethod` 声明，可以通过类名直接调用。

```python
class Converter:
    @staticmethod
    def celsius_to_fahrenheit(c: float) -> float:
        return c * 9 / 5 + 32

Converter.celsius_to_fahrenheit(100)  # 212.0
```

**本课为什么用它：** `_convert_messages()`、`_convert_tools()`、`_map_response()`、`_merge_consecutive_roles()` 都是纯转换函数，不需要访问 `self`，用 `@staticmethod` 明确这一点，也便于在测试中直接调用。

### 返回 `tuple`（元组解包）

函数可以返回一个元组，调用方用多个变量同时接收。

```python
def split_name(full: str) -> tuple[str, str]:
    parts = full.split(" ", 1)
    return parts[0], parts[1]

first, last = split_name("John Doe")
```

**本课为什么用它：** `_convert_messages()` 返回 `tuple[str, list[dict]]`，同时返回提取出的系统提示词文本和转换后的消息列表。调用方用 `system_text, anthropic_msgs = self._convert_messages(messages)` 一行解包。

### `dict.get()`（安全字典访问）

`dict.get(key, default)` 在键不存在时返回默认值而不是抛出 `KeyError`。

```python
msg = {"role": "user", "content": "Hello"}
role = msg.get("role", "user")       # "user"
tool_calls = msg.get("tool_calls")   # None（键不存在）
```

**本课为什么用它：** 消息字典的结构不固定（有的消息有 `tool_calls`，有的没有），用 `.get()` 安全地尝试获取可选字段，避免 `KeyError` 异常。

### `str.join()`（字符串拼接）

`"分隔符".join(列表)` 将字符串列表拼接为一个字符串。比在循环中用 `+=` 高效得多。

```python
parts = ["Hello", "World", "!"]
result = " ".join(parts)   # "Hello World !"
result = "".join(parts)    # "HelloWorld!"
result = "\n\n".join(parts) # 用两个换行连接
```

**本课为什么用它：** `_convert_messages()` 用 `"\n\n".join(system_parts)` 将多个系统消息合并为一个文本；`chat_stream()` 用 `"".join(content_parts)` 将流式接收的文本片段拼接为完整响应。

### `getattr()`（动态属性访问）

`getattr(obj, name, default)` 通过字符串名称获取属性，属性不存在时返回默认值。

```python
class Event:
    type = "content_block_delta"

event = Event()
event_type = getattr(event, "type", None)  # "content_block_delta"
missing = getattr(event, "data", None)     # None
```

**本课为什么用它：** Anthropic 流式事件的结构是动态的，不同事件类型有不同的属性。用 `getattr(event, "type", None)` 安全地获取事件类型，用 `getattr(delta, "type", None)` 检查 delta 的类型，不会因为属性不存在而崩溃。

### 适配器模式（设计模式）

适配器模式将一个接口转换为另一个接口，让原本不兼容的类可以协同工作。就像电源适配器把美标插头转换成国标插座一样。

```python
# 客户端期望的统一接口
class LLMProvider(ABC):
    async def chat(self, messages) -> LLMResponse: ...

# 适配器：把 Anthropic API 转换为统一接口
class AnthropicProvider(LLMProvider):
    async def chat(self, messages) -> LLMResponse:
        system, msgs = self._convert_messages(messages)  # 转换格式
        response = await self.client.messages.create(...)  # 调用 Anthropic
        return self._map_response(response)               # 转换回统一格式
```

**本课为什么用它：** `AnthropicProvider` 就是一个适配器，它把 Agent 使用的 OpenAI 风格消息格式转换为 Anthropic API 需要的格式，再把 Anthropic 的响应转换回统一的 `LLMResponse`。Agent 完全不知道底层用的是哪个 LLM。

### OOP 继承与 `super().__init__()`

子类通过 `super()` 调用父类的构造函数，确保父类的初始化逻辑被正确执行。

```python
class Base:
    def __init__(self, name: str):
        self.name = name

class Child(Base):
    def __init__(self, name: str, extra: int):
        super().__init__(name)  # 先初始化父类的 name
        self.extra = extra      # 再初始化子类特有的属性
```

**本课为什么用它：** `AnthropicProvider` 继承 `LLMProvider`，在 `__init__` 中用 `super().__init__()` 设置 `api_key`、`api_base`、`generation` 等父类属性，然后添加 `_default_model` 和 `_client` 等 Anthropic 特有的属性。

### `json` 模块 — `json.loads()` 和 `json.dumps()`

`json.loads()` 将 JSON 字符串解析为 Python 对象，`json.dumps()` 将 Python 对象序列化为 JSON 字符串。

```python
import json

# 解析
data = json.loads('{"name": "Alice", "age": 30}')
print(data["name"])  # "Alice"

# 序列化
text = json.dumps(data)
print(text)  # '{"name": "Alice", "age": 30}'
```

**本课为什么用它：** OpenAI 的工具调用参数是 JSON 字符串（如 `'{"path": "."}'`），而 Anthropic 需要字典。转换时用 `json.loads()` 解析字符串为字典；反向转换时用 `json.dumps()` 序列化。`try/except json.JSONDecodeError` 处理可能的格式错误。

### `@property` 与延迟初始化

`@property` 让方法像属性一样访问（不加括号）。结合 `if self._x is None` 模式实现延迟初始化，只在第一次使用时创建资源。

```python
class Service:
    def __init__(self):
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import heavy_library
            self._client = heavy_library.Client()
        return self._client
```

**本课为什么用它：** `AnthropicProvider.client` 属性在第一次访问时才创建 `AsyncAnthropic` 客户端，避免在构造时就建立网络连接。同时将 `import anthropic` 放在方法内部，用户没装 anthropic 包也不会在导入时报错。

### `pytest` 测试

`pytest` 是 Python 最常用的测试框架，用简单的 `assert` 语句验证结果。测试函数以 `test_` 开头即可被自动发现。

```python
def test_addition():
    assert 1 + 1 == 2

def test_string():
    assert "hello".upper() == "HELLO"
```

**本课为什么用它：** 测试用例直接调用 `AnthropicProvider` 的静态方法（如 `_convert_messages`、`_convert_tools`、`_map_stop_reason`），验证消息转换、工具格式转换、停止原因映射等逻辑是否正确，不需要真实的 API 调用。
