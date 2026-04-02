# 课程 6：提供者抽象 -- 多 LLM 支持

**目标：** 将 LLM 通信抽取为可插拔的提供者系统，以便支持任何后端。

**你将学到：**
- LLMProvider 抽象基类
- LLMResponse 和 GenerationSettings 数据类
- 指数退避的重试逻辑，应对瞬态错误
- OpenAICompatProvider（适用于 OpenAI、DeepSeek、Groq、Ollama 等）
- 带有提供者规格的 ProviderRegistry

**新建文件：**
- `ultrabot/providers/base.py` -- LLMProvider ABC、LLMResponse、重试逻辑
- `ultrabot/providers/openai_compat.py` -- OpenAI 兼容提供者
- `ultrabot/providers/registry.py` -- 静态提供者规格注册表
- `ultrabot/providers/__init__.py` -- 公共接口

### 步骤 1：定义提供者接口

关键洞察：每个 LLM 提供者（OpenAI、Anthropic、DeepSeek、Ollama）做的事情都一样 -- 接收消息，返回响应。区别在于认证方式、URL 和消息格式。因此我们抽象出接口：

```python
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
```

### 步骤 2：构建 OpenAI 兼容提供者

这单个类适用于 OpenAI、DeepSeek、Groq、Ollama、OpenRouter 以及任何其他支持 `/v1/chat/completions` 协议的服务：

```python
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
        default_model: str = "gpt-4o-mini",
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
```

### 步骤 3：提供者注册表

```python
# ultrabot/providers/registry.py
"""已知 LLM 提供者规格的静态注册表。

取自 ultrabot/providers/registry.py。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProviderSpec:
    """受支持 LLM 提供者的不可变描述符。

    取自 ultrabot/providers/registry.py 第 13-30 行。
    """
    name: str
    keywords: tuple[str, ...] = ()
    env_key: str = ""
    display_name: str = ""
    backend: str = "openai_compat"  # "openai_compat" | "anthropic"
    default_api_base: str = ""
    is_local: bool = False


# 规范提供者注册表（取自第 37-154 行）
PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        name="openai",
        keywords=("openai", "gpt", "o1", "o3", "o4"),
        env_key="OPENAI_API_KEY",
        display_name="OpenAI",
        default_api_base="https://api.openai.com/v1",
    ),
    ProviderSpec(
        name="anthropic",
        keywords=("anthropic", "claude"),
        env_key="ANTHROPIC_API_KEY",
        display_name="Anthropic",
        backend="anthropic",
        default_api_base="https://api.anthropic.com",
    ),
    ProviderSpec(
        name="deepseek",
        keywords=("deepseek",),
        env_key="DEEPSEEK_API_KEY",
        display_name="DeepSeek",
        default_api_base="https://api.deepseek.com/v1",
    ),
    ProviderSpec(
        name="groq",
        keywords=("groq",),
        env_key="GROQ_API_KEY",
        display_name="Groq",
        default_api_base="https://api.groq.com/openai/v1",
    ),
    ProviderSpec(
        name="ollama",
        keywords=("ollama",),
        display_name="Ollama (local)",
        default_api_base="http://localhost:11434/v1",
        is_local=True,
    ),
)


def find_by_name(name: str) -> ProviderSpec | None:
    """按名称查找提供者规格（不区分大小写）。"""
    for spec in PROVIDERS:
        if spec.name == name.lower():
            return spec
    return None


def find_by_keyword(keyword: str) -> ProviderSpec | None:
    """按关键词匹配查找提供者规格。"""
    kw = keyword.lower()
    for spec in PROVIDERS:
        if kw in spec.keywords:
            return spec
    return None
```

### 步骤 4：重构 Agent 以使用提供者

现在 Agent 使用 `LLMProvider` 而不是直接与 OpenAI 通信：

```python
# 在 ultrabot/agent.py 中 -- 更新 __init__ 以接受提供者：

class Agent:
    def __init__(
        self,
        provider: LLMProvider,  # <-- 之前是：OpenAI 客户端
        model: str = "gpt-4o-mini",
        system_prompt: str = SYSTEM_PROMPT,
        max_iterations: int = 10,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self._provider = provider
        self._model = model
        # ... 其余不变
```

### 测试

```python
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
    spec = find_by_name("openai")
    assert spec is not None
    assert spec.name == "openai"

    assert find_by_name("nonexistent") is None


def test_find_by_keyword():
    """find_by_keyword 按关键词元组匹配。"""
    spec = find_by_keyword("gpt")
    assert spec is not None
    assert spec.name == "openai"

    spec = find_by_keyword("claude")
    assert spec is not None
    assert spec.name == "anthropic"


def test_all_providers_have_required_fields():
    """每个已注册的提供者都有 name 和 backend。"""
    for spec in PROVIDERS:
        assert spec.name
        assert spec.backend in ("openai_compat", "anthropic")
```

### 检查点

```python
import asyncio
from ultrabot.providers.openai_compat import OpenAICompatProvider
from ultrabot.providers.base import GenerationSettings

# 为 OpenAI 创建提供者
provider = OpenAICompatProvider(
    api_key="your-key-here",
    api_base="https://api.openai.com/v1",
    generation=GenerationSettings(temperature=0.7, max_tokens=1024),
    default_model="gpt-4o-mini",
)

# 同一个提供者类也适用于 DeepSeek！
deepseek = OpenAICompatProvider(
    api_key="your-deepseek-key",
    api_base="https://api.deepseek.com/v1",
    default_model="deepseek-chat",
)
```

通过更改配置即可在不同提供者之间切换：

```json
{
  "agents": {
    "defaults": {
      "model": "gpt-4o",
      "provider": "openai"
    }
  }
}
```

### 本课成果

一个提供者抽象层，具备：
- `LLMProvider` ABC，任何后端都可以实现
- `LLMResponse` 标准化信封（无论提供者是谁，格式都一样）
- 指数退避的重试逻辑，应对瞬态错误（429、503 等）
- `OpenAICompatProvider`，开箱即用适配 10+ 种服务
- `ProviderRegistry` 将提供者名称映射到规格

---
