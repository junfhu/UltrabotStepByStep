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
