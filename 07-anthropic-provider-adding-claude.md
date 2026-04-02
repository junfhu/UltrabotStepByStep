# 课程 7：Anthropic 提供者 -- 添加 Claude

**目标：** 添加原生 Anthropic（Claude）支持，了解不同 LLM API 之间的差异。

**你将学到：**
- Anthropic 的消息格式与 OpenAI 的区别
- 系统提示词提取（Anthropic 将其放在消息数组之外）
- 工具使用格式转换（OpenAI functions -> Anthropic tool_use 块）
- 带有内容块组装的流式输出
- 用于标准化不同 API 的适配器模式

**新建文件：**
- `ultrabot/providers/anthropic_provider.py` -- 原生 Anthropic 提供者

### 步骤 1：安装 Anthropic SDK

```bash
pip install anthropic
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
    ) -> None:
        super().__init__(api_key=api_key, api_base=api_base, generation=generation)
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
        model = model or "claude-sonnet-4-20250514"

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
        model = model or "claude-sonnet-4-20250514"
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

### 检查点

```python
import asyncio
from ultrabot.providers.anthropic_provider import AnthropicProvider

# 创建 Anthropic 提供者
provider = AnthropicProvider(api_key="sk-ant-...")

# 与 OpenAICompatProvider 接口完全相同！
response = asyncio.run(provider.chat(
    messages=[
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "What is Python?"},
    ],
    model="claude-sonnet-4-20250514",
))

print(response.content)
```

只需修改一行代码即可在 GPT-4o 和 Claude 之间切换：

```python
# OpenAI
provider = OpenAICompatProvider(api_key="sk-...", default_model="gpt-4o")

# Anthropic -- Agent 接口完全相同
provider = AnthropicProvider(api_key="sk-ant-...")
```

### 本课成果

一个原生 Anthropic 提供者，处理 OpenAI 和 Anthropic API 之间的所有格式差异。适配器模式意味着我们的 Agent 类不关心它在和哪个 LLM 对话 -- 两个提供者都返回相同的 `LLMResponse` 格式。这直接对应 `ultrabot/providers/anthropic_provider.py`。

---
