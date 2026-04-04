# ultrabot/providers/base.py
"""LLM 提供者的基类。

取自 ultrabot/providers/base.py。
"""
from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine


# -- 数据传输对象 --

@dataclass
class ToolCallRequest:
    """来自模型响应的单个工具调用。

    取自 ultrabot/providers/base.py 第 20-38 行。
    """
    id: str
    name: str
    arguments: dict[str, Any]

    def to_openai_tool_call(self) -> dict[str, Any]:
        """序列化为 OpenAI 传输格式。"""
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments, ensure_ascii=False),
            },
        }


@dataclass
class LLMResponse:
    """每个提供者都返回的标准化响应信封。

    取自 ultrabot/providers/base.py 第 41-55 行。
    """
    content: str | None = None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


@dataclass
class GenerationSettings:
    """默认的生成超参数。

    取自 ultrabot/providers/base.py 第 57-63 行。
    """
    temperature: float = 0.7
    max_tokens: int = 4096
    reasoning_effort: str | None = None


# -- 瞬态错误检测 --

_TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
_TRANSIENT_MARKERS = (
    "rate limit", "rate_limit", "overloaded", "too many requests",
    "server error", "bad gateway", "service unavailable", "timeout",
    "connection error",
)


# -- 抽象提供者 --

class LLMProvider(ABC):
    """所有 LLM 后端的抽象基类。

    子类实现 chat()；流式输出和重试包装器已提供。

    取自 ultrabot/providers/base.py 第 93-277 行。
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        generation: GenerationSettings | None = None,
    ) -> None:
        self.api_key = api_key
        self.api_base = api_base
        self.generation = generation or GenerationSettings()

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        """发送聊天补全请求并返回标准化响应。"""

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        on_content_delta: Callable[[str], Coroutine[Any, Any, None]] | None = None,
    ) -> LLMResponse:
        """流式输出变体。如果未被覆盖则回退到 chat()。"""
        return await self.chat(messages=messages, tools=tools, model=model,
                               max_tokens=max_tokens, temperature=temperature)

    # -- 重试包装器 --

    _DEFAULT_DELAYS = (1.0, 2.0, 4.0)

    async def chat_stream_with_retry(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        on_content_delta: Callable[[str], Coroutine[Any, Any, None]] | None = None,
        retries: int | None = None,
    ) -> LLMResponse:
        """带自动重试和指数退避的 chat_stream()。

        取自 ultrabot/providers/base.py 第 196-224 行。
        """
        delays = self._DEFAULT_DELAYS
        max_attempts = (retries if retries is not None else len(delays)) + 1

        last_exc: BaseException | None = None
        for attempt in range(max_attempts):
            try:
                return await self.chat_stream(
                    messages=messages, tools=tools, model=model,
                    on_content_delta=on_content_delta,
                )
            except Exception as exc:
                last_exc = exc
                if not self._is_transient_error(exc) or attempt >= max_attempts - 1:
                    raise
                delay = delays[min(attempt, len(delays) - 1)]
                await asyncio.sleep(delay)

        raise last_exc  # type: ignore

    @staticmethod
    def _is_transient_error(exc: BaseException) -> bool:
        """检测可重试错误（速率限制、超时等）。

        取自 ultrabot/providers/base.py 第 260-277 行。
        """
        status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
        if status is not None and status in _TRANSIENT_STATUS_CODES:
            return True

        exc_name = type(exc).__name__.lower()
        if "timeout" in exc_name or "connection" in exc_name:
            return True

        message = str(exc).lower()
        return any(marker in message for marker in _TRANSIENT_MARKERS)
