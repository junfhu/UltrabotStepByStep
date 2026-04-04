# ultrabot/providers/openai_compat.py
"""OpenAI 兼容提供者。

适用于 OpenAI、DeepSeek、Groq、Ollama、vLLM、OpenRouter 等。

取自 ultrabot/providers/openai_compat.py。
"""
from __future__ import annotations

import json
from typing import Any, Callable, Coroutine

from ultrabot.providers.base import (
    GenerationSettings, LLMProvider, LLMResponse, ToolCallRequest,
)


class OpenAICompatProvider(LLMProvider):
    """适用于任何 OpenAI 兼容 API 的提供者。

    取自 ultrabot/providers/openai_compat.py 第 21-268 行。
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
        """延迟创建 AsyncOpenAI 客户端。

        取自 ultrabot/providers/openai_compat.py 第 38-50 行。
        """
        if self._client is None:
            import openai
            self._client = openai.AsyncOpenAI(
                api_key=self.api_key or "not-needed",
                base_url=self.api_base,
                max_retries=0,  # 我们自己处理重试
            )
        return self._client

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        """非流式聊天补全。

        取自 ultrabot/providers/openai_compat.py 第 68-105 行。
        """
        kwargs: dict[str, Any] = {
            "model": model or self._default_model,
            "messages": messages,
            "temperature": temperature or self.generation.temperature,
            "max_tokens": max_tokens or self.generation.max_tokens,
        }
        if tools:
            kwargs["tools"] = tools

        response = await self.client.chat.completions.create(**kwargs)
        return self._map_response(response)

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        on_content_delta: Callable[[str], Coroutine[Any, Any, None]] | None = None,
    ) -> LLMResponse:
        """流式聊天补全。

        取自 ultrabot/providers/openai_compat.py 第 109-200 行。
        """
        kwargs: dict[str, Any] = {
            "model": model or self._default_model,
            "messages": messages,
            "temperature": temperature or self.generation.temperature,
            "max_tokens": max_tokens or self.generation.max_tokens,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools

        stream = await self.client.chat.completions.create(**kwargs)

        content_parts: list[str] = []
        tool_call_map: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None

        async for chunk in stream:
            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta
            if chunk.choices[0].finish_reason:
                finish_reason = chunk.choices[0].finish_reason

            # 内容 token
            if delta.content:
                content_parts.append(delta.content)
                if on_content_delta:
                    await on_content_delta(delta.content)

            # 工具调用增量（以流式方式增量传输）
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_call_map:
                        tool_call_map[idx] = {"id": "", "name": "", "arguments": ""}
                    entry = tool_call_map[idx]
                    if tc_delta.id:
                        entry["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            entry["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            entry["arguments"] += tc_delta.function.arguments

        # 组装工具调用
        tool_calls = self._assemble_tool_calls(tool_call_map)

        return LLMResponse(
            content="".join(content_parts) or None,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
        )

    @staticmethod
    def _map_response(response: Any) -> LLMResponse:
        """将 OpenAI ChatCompletion 转换为 LLMResponse。"""
        choice = response.choices[0]
        msg = choice.message

        tool_calls = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except json.JSONDecodeError:
                    args = {"_raw": tc.function.arguments}
                tool_calls.append(ToolCallRequest(
                    id=tc.id, name=tc.function.name, arguments=args,
                ))

        usage = {}
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        return LLMResponse(
            content=msg.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason,
            usage=usage,
        )

    @staticmethod
    def _assemble_tool_calls(tool_call_map: dict[int, dict]) -> list[ToolCallRequest]:
        """解析累积的流式工具调用片段。"""
        calls = []
        for idx in sorted(tool_call_map):
            entry = tool_call_map[idx]
            try:
                args = json.loads(entry["arguments"]) if entry["arguments"] else {}
            except json.JSONDecodeError:
                args = {"_raw": entry["arguments"]}
            calls.append(ToolCallRequest(
                id=entry["id"], name=entry["name"], arguments=args,
            ))
        return calls
