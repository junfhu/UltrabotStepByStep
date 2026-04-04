# Ultrabot：30 课程开发指南
**从零开始构建一个生产级 AI 助手框架。**
本指南将带你从"向 LLM 问好"一步步走到一个完整的多提供者、多通道 AI 智能体，具备工具调用、记忆、安全防护和 Web 界面。每节课程都建立在上一节课的基础之上。每节课都包含可运行的代码和测试。  
本教程的主要思路来自于
- Nanobot (https://github.com/HKUDS/nanobot)
- Learn-Claude-Code (https://github.com/shareAI-lab/learn-claude-code/)

本课程设计由AI辅助下完成，因为课程自身也在不停修正，请参考 https://github.com/junfhu/UltrabotStepByStep，如果您觉得对您有帮助，请帮助点亮一颗星。  
本课程中使用的大模型提供商是火山引擎Code Plan，如果正好你也需要，可以使用我的邀请码获取9折优惠 https://volcengine.com/L/_01BJCkKdMc/  邀请码：HHCDB4J4）  



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
        # `openai_compatible` 指任何兼容 OpenAI SDK / OpenAI API 的提供者。
        name="openai_compatible",
        keywords=("openai", "compatible", "gpt", "o1", "o3", "o4", "minimax"),
        env_key="OPENAI_API_KEY",
        display_name="OpenAI-Compatible",
        default_api_base="https://ark.cn-beijing.volces.com/api/coding/v3",
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
        model: str = "minimax-m2.5",
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
```

### 检查点

测试：(确保已通过 `pip install -e .` 安装包)

```python
import asyncio
from ultrabot.providers.openai_compat import OpenAICompatProvider
from ultrabot.providers.base import GenerationSettings

# 为 OpenAI 创建提供者
provider = OpenAICompatProvider(
    api_key="your-key-here",
    api_base="https://ark.cn-beijing.volces.com/api/coding/v3",
    generation=GenerationSettings(temperature=0.7, max_tokens=1024),
    default_model="minimax-m2.5",
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
      "model": "minimax-m2.5",
      "provider": "openai_compatible"
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

## 本课使用的 Python 知识

### `from __future__ import annotations`

这是 Python 的**延迟注解求值**特性。正常情况下，Python 在定义函数时就会解析类型注解，这可能导致「类还没定义完就被引用」的错误。加上这行后，所有注解都变成字符串，直到真正需要时才解析。

```python
from __future__ import annotations

class Node:
    # 没有 future annotations，这里会报错，因为 Node 还没定义完
    def add_child(self, child: Node) -> None:
        pass
```

**本课为什么用它：** 代码中大量使用了 `str | None`、`list[dict[str, Any]]` 等现代类型注解语法，`from __future__ import annotations` 保证这些注解在 Python 3.9 及以下版本也不会报错。

### `abc.ABC` 和 `@abstractmethod`（抽象基类）

`ABC`（Abstract Base Class）是 Python 中定义「接口」的方式。继承了 `ABC` 的类不能直接实例化，必须由子类实现所有 `@abstractmethod` 标记的方法。

```python
from abc import ABC, abstractmethod

class Animal(ABC):
    @abstractmethod
    def speak(self) -> str:
        """子类必须实现这个方法"""

class Dog(Animal):
    def speak(self) -> str:
        return "Woof!"

# animal = Animal()  # 报错！不能实例化抽象类
dog = Dog()           # 可以，因为实现了 speak()
```

**本课为什么用它：** `LLMProvider` 作为抽象基类定义了所有 LLM 后端的统一接口（`chat()`），不同的提供者（OpenAI、Anthropic 等）只需继承并实现这个方法，调用方不需要关心底层用的是哪个提供者。

### `@dataclass` 和 `field(default_factory=...)`（数据类）

`@dataclass` 装饰器自动为类生成 `__init__`、`__repr__`、`__eq__` 等方法，省去大量样板代码。`field(default_factory=list)` 用于可变默认值（如列表、字典），避免多个实例共享同一个对象。

```python
from dataclasses import dataclass, field

@dataclass
class Config:
    name: str
    tags: list[str] = field(default_factory=list)  # 每个实例有自己的列表
    temperature: float = 0.7                        # 不可变类型可以直接给默认值

c1 = Config(name="test")
c2 = Config(name="demo")
c1.tags.append("a")
print(c2.tags)  # [] — 不会被 c1 影响
```

**本课为什么用它：** `LLMResponse`、`GenerationSettings`、`ToolCallRequest`、`ProviderSpec` 都是纯数据载体，用 `@dataclass` 可以简洁地定义它们。`LLMResponse` 的 `tool_calls` 和 `usage` 字段用了 `default_factory`，确保每个响应对象都有自己独立的列表和字典。

### `@dataclass(frozen=True)`（不可变数据类）

`frozen=True` 让数据类的实例在创建后不可修改，类似 `namedtuple`。如果尝试修改属性，会抛出 `FrozenInstanceError`。

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class Point:
    x: int
    y: int

p = Point(1, 2)
# p.x = 3  # 报错！FrozenInstanceError
```

**本课为什么用它：** `ProviderSpec` 用了 `frozen=True`，因为提供者规格（名称、关键词、默认 URL 等）是注册时就确定的常量，不应该被运行时修改。

### `typing` 模块 — `Any`、`Callable`、`Coroutine`

`typing` 模块提供类型注解工具。`Any` 表示任意类型；`Callable` 描述可调用对象的签名；`Coroutine` 描述协程的类型。

```python
from typing import Any, Callable, Coroutine

# Any：接受任意类型
def process(data: Any) -> None: ...

# Callable[[参数类型], 返回类型]
def apply(func: Callable[[int], str], value: int) -> str:
    return func(value)

# 描述一个异步回调函数
callback: Callable[[str], Coroutine[Any, Any, None]]
```

**本课为什么用它：** `chat()` 方法的参数 `messages: list[dict[str, Any]]` 用 `Any` 表示消息字典的值可以是任何类型。`on_content_delta` 回调用 `Callable[[str], Coroutine[Any, Any, None]]` 精确描述了「接收一个字符串、返回一个协程」的签名。

### `str | None` 联合类型（Python 3.10+ 语法）

Python 3.10 引入了用 `|` 表示联合类型的简洁语法，替代了旧的 `Optional[str]` 或 `Union[str, None]`。

```python
# Python 3.10+ 写法
def greet(name: str | None = None) -> str:
    return f"Hello, {name or 'World'}!"

# 等价于旧写法
from typing import Optional
def greet(name: Optional[str] = None) -> str: ...
```

**本课为什么用它：** 代码中大量使用 `str | None` 表示可选参数，如 `api_key: str | None = None`、`content: str | None = None`，比 `Optional` 写法更简洁直观。

### `async/await` 和 `asyncio`（异步编程）

`async def` 定义协程函数，`await` 暂停当前协程等待另一个协程完成。`asyncio` 是 Python 的异步 I/O 框架，`asyncio.sleep()` 是非阻塞的等待。

```python
import asyncio

async def fetch_data() -> str:
    await asyncio.sleep(1)  # 非阻塞等待 1 秒
    return "data"

async def main():
    result = await fetch_data()
    print(result)

asyncio.run(main())
```

**本课为什么用它：** LLM API 调用是网络 I/O 操作，可能需要等待数秒。使用 `async/await` 可以在等待一个请求返回时处理其他任务，特别是在重试逻辑中 `await asyncio.sleep(delay)` 不会阻塞整个程序。

### `async for`（异步迭代）

`async for` 用于遍历异步可迭代对象，每次迭代都可能涉及异步操作（如等待网络数据）。

```python
async for chunk in stream:
    print(chunk)
```

**本课为什么用它：** `chat_stream()` 中使用 `async for chunk in stream` 逐块接收 LLM 的流式响应，每个 chunk 到达时立即处理，实现实时输出效果。

### `@property`（属性装饰器）和延迟初始化

`@property` 把方法变成属性访问的样子，调用时不需要加括号。常用于延迟初始化（lazy initialization）——第一次访问时才创建对象。

```python
class Database:
    def __init__(self):
        self._conn = None

    @property
    def connection(self):
        if self._conn is None:
            self._conn = create_connection()  # 第一次访问时才连接
        return self._conn

db = Database()
db.connection  # 不加括号，像访问属性一样
```

**本课为什么用它：** `OpenAICompatProvider.client` 属性使用延迟初始化，只有在第一次实际调用 API 时才创建 `AsyncOpenAI` 客户端，避免了在导入或构造时就建立网络连接。

### `@staticmethod`（静态方法）

`@staticmethod` 定义不需要访问实例（`self`）或类（`cls`）的方法，本质上是放在类命名空间里的普通函数。

```python
class MathHelper:
    @staticmethod
    def add(a: int, b: int) -> int:
        return a + b

MathHelper.add(1, 2)  # 不需要创建实例
```

**本课为什么用它：** `_is_transient_error()`、`_map_response()`、`_assemble_tool_calls()` 都是纯逻辑计算，不依赖任何实例状态，用 `@staticmethod` 明确表达这一点，也方便在测试中直接调用（如 `LLMProvider._is_transient_error(exc)`）。

### `frozenset`（不可变集合）

`frozenset` 是不可变版本的 `set`，创建后不能添加或删除元素。它可以用作字典的键或放入另一个集合中。

```python
ALLOWED_CODES = frozenset({200, 201, 204})
print(429 in ALLOWED_CODES)  # False — 查找速度 O(1)
```

**本课为什么用它：** `_TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503, 504})` 定义了可重试的 HTTP 状态码。用 `frozenset` 而非 `set` 表示这是一个常量，不会被意外修改，同时 `in` 查找的时间复杂度为 O(1)。

### `getattr()` 动态属性访问

`getattr(obj, name, default)` 在运行时通过字符串名称获取对象的属性。如果属性不存在，返回默认值而不是报错。

```python
class Error:
    status_code = 429

exc = Error()
status = getattr(exc, "status_code", None)  # 429
missing = getattr(exc, "headers", None)      # None（不存在，返回默认值）
```

**本课为什么用它：** `_is_transient_error()` 需要检查异常对象是否带有 `status_code` 或 `status` 属性。不同的 HTTP 库抛出的异常类型不同，用 `getattr` 可以安全地尝试获取，不存在就返回 `None`。

### `any()` 与生成器表达式

`any()` 接受一个可迭代对象，只要其中有一个元素为真就返回 `True`。配合生成器表达式可以高效地检测匹配。

```python
markers = ("error", "timeout", "refused")
message = "connection timeout occurred"
has_match = any(m in message for m in markers)  # True
```

**本课为什么用它：** `_is_transient_error()` 用 `any(marker in message for marker in _TRANSIENT_MARKERS)` 检查错误消息中是否包含任何一个瞬态错误标记词，简洁且在找到第一个匹配后就停止搜索。

### `**kwargs` 字典解包

`**kwargs` 将字典解包为关键字参数传给函数。可以动态构建参数列表。

```python
params = {"model": "gpt-4", "temperature": 0.7}
# 等价于 client.create(model="gpt-4", temperature=0.7)
response = client.create(**params)
```

**本课为什么用它：** `chat()` 方法先将参数构建为 `kwargs` 字典，再用 `**kwargs` 传给 `client.chat.completions.create()`。这允许有条件地添加参数（如只有在有工具时才加 `tools` 键）。

### OOP 继承与 `super().__init__()`

子类通过 `super()` 调用父类的方法，确保初始化链正确执行。

```python
class Base:
    def __init__(self, name: str):
        self.name = name

class Child(Base):
    def __init__(self, name: str, age: int):
        super().__init__(name)  # 先初始化父类
        self.age = age
```

**本课为什么用它：** `OpenAICompatProvider` 继承 `LLMProvider`，在 `__init__` 中通过 `super().__init__()` 调用父类来初始化 `api_key`、`api_base`、`generation` 等公共属性，自己只添加 `_default_model` 和 `_client` 等子类特有的属性。

### `json.dumps()` 和 `json.loads()`（JSON 序列化）

`json.dumps()` 将 Python 对象转为 JSON 字符串，`json.loads()` 将 JSON 字符串解析为 Python 对象。`ensure_ascii=False` 允许输出非 ASCII 字符（如中文）。

```python
import json

data = {"name": "你好", "value": 42}
text = json.dumps(data, ensure_ascii=False)  # '{"name": "你好", "value": 42}'
parsed = json.loads(text)                     # {'name': '你好', 'value': 42}
```

**本课为什么用它：** 工具调用的参数在 OpenAI API 中以 JSON 字符串传输，需要用 `json.dumps()` 序列化和 `json.loads()` 反序列化。`try/except json.JSONDecodeError` 处理 LLM 可能返回的格式不正确的 JSON。

### `try/except` 异常处理

`try/except` 捕获并处理异常，防止程序崩溃。可以捕获特定类型的异常。

```python
try:
    result = json.loads(text)
except json.JSONDecodeError:
    result = {"_raw": text}  # 解析失败时的回退方案
```

**本课为什么用它：** 重试逻辑中用 `try/except Exception` 捕获 API 调用失败；JSON 解析时用 `try/except json.JSONDecodeError` 处理 LLM 返回的不合法 JSON。这保证了程序在遇到错误时能优雅降级而不是直接崩溃。

### `pytest` 和 `unittest.mock`（测试框架）

`pytest` 是 Python 最流行的测试框架。`unittest.mock` 提供 `AsyncMock`（模拟异步函数）、`MagicMock`（通用模拟对象）和 `patch`（临时替换对象）。

```python
import pytest
from unittest.mock import AsyncMock

async def test_something():
    mock_api = AsyncMock(return_value="hello")
    result = await mock_api()
    assert result == "hello"
    mock_api.assert_called_once()
```

**本课为什么用它：** 测试不需要真实的 API 调用。`AsyncMock` 模拟异步 LLM 客户端，`assert` 验证数据类行为、序列化结果、错误检测逻辑等是否正确。

### 延迟导入（函数内部 `import`）

将 `import` 语句放在函数内部而不是文件顶部，实现按需加载。

```python
@property
def client(self):
    if self._client is None:
        import openai  # 只有真正需要时才导入
        self._client = openai.AsyncOpenAI(...)
    return self._client
```

**本课为什么用它：** `openai` 库只在实际创建客户端时才导入。这样即使用户没有安装 `openai` 包，只要不使用 `OpenAICompatProvider`，程序也不会报错。这对支持多个可选后端的提供者系统非常重要。
