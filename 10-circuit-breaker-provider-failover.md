# Ultrabot：30 课程开发指南
**从零开始构建一个生产级 AI 助手框架。**
本指南将带你从"向 LLM 问好"一步步走到一个完整的多提供者、多通道 AI 智能体，具备工具调用、记忆、安全防护和 Web 界面。每节课程都建立在上一节课的基础之上。每节课都包含可运行的代码和测试。  
本教程的主要思路来自于Nanobot(https://github.com/HKUDS/nanobot)以及Learn-Claude-Code(https://github.com/shareAI-lab/learn-claude-code/)，所以对应的叫做Ultrabot。  
本课程设计由AI辅助下完成，因为课程自身也在不停修正，请参考https://github.com/junfhu/UltrabotStepByStep的最新版本，如果您觉得对您有帮助，请帮助点亮一颗星。  
本课程中使用的大模型提供商是火山引擎Code Plan，如果正好你也需要，可以使用我的邀请码获取9折优惠 https://volcengine.com/L/_01BJCkKdMc/  邀请码：HHCDB4J4）  



# 课程 10：熔断器 + 提供者故障转移

**目标：** 通过为每个提供者添加熔断器并自动故障转移到健康的替代方案，保护智能体免受级联 LLM 故障的影响。

**你将学到：**
- 熔断器状态机模式（CLOSED → OPEN → HALF_OPEN）
- 可配置阈值的失败计数
- 使用 `time.monotonic()` 的基于时间的恢复
- 提供者与模型的一对多映射（`ProviderConfig.models`）
- 动态自定义提供者 — 在 `config.json` 中添加 OpenRouter、百炼等，无需修改代码
- 通过熔断器路由请求的 `ProviderManager`
- 基于优先级的故障转移链

**修改文件：**
- `ultrabot/providers/registry.py` — 新增 OpenRouter、百炼、火山引擎的 `ProviderSpec`
- `ultrabot/config/schema.py` — `ProviderConfig` 新增 `models` 字段；`ProvidersConfig` 支持动态自定义提供者（`extra="allow"`）；`Config.get_provider()` 支持精确匹配 + 关键字回退
- `ultrabot/providers/base.py` — `LLMProvider` 新增 `chat_with_retry()` 重试包装器

**新建文件：**
- `ultrabot/providers/circuit_breaker.py` — `CircuitState` 枚举 + `CircuitBreaker`
- `ultrabot/providers/manager.py` — `ProviderManager` 编排器（含 `_register_from_config()`）

### 步骤 1：熔断器状态

熔断器有三种状态：

```
CLOSED  ──[达到失败阈值]──>  OPEN
OPEN    ──[超时时间已过]──>  HALF_OPEN
HALF_OPEN ──[成功]────────>  CLOSED
HALF_OPEN ──[失败]────────>  OPEN
```

创建 `ultrabot/providers/circuit_breaker.py`：

```python
"""用于 LLM 提供者健康跟踪的熔断器模式。"""

from __future__ import annotations

import time
from enum import Enum

from loguru import logger


class CircuitState(Enum):
    """熔断器的可能状态。"""
    CLOSED = "closed"       # 健康 — 请求正常通过
    OPEN = "open"           # 已熔断 — 请求被拒绝
    HALF_OPEN = "half_open" # 探测中 — 允许有限的请求通过


class CircuitBreaker:
    """每个提供者的熔断器。

    状态机：
        CLOSED  --[failure_threshold 次连续失败]--> OPEN
        OPEN    --[recovery_timeout 时间已过]-----> HALF_OPEN
        HALF_OPEN --[成功]------------------------> CLOSED
        HALF_OPEN --[失败]------------------------> OPEN
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_max_calls: int = 3,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls

        self._state: CircuitState = CircuitState.CLOSED
        self._consecutive_failures: int = 0
        self._last_failure_time: float = 0.0
        self._half_open_calls: int = 0
        self._reopened_from_half_open: bool = False
```

### 步骤 2：记录成功和失败

```python
    def record_success(self) -> None:
        """一次成功的调用会重置熔断器。"""
        if self._state == CircuitState.HALF_OPEN:
            logger.info("Circuit breaker closing after successful probe")
            self._transition(CircuitState.CLOSED)
        self._consecutive_failures = 0
        self._half_open_calls = 0

    def record_failure(self) -> None:
        """一次失败的调用 — 当达到阈值时触发熔断。"""
        self._consecutive_failures += 1

        if self._state == CircuitState.HALF_OPEN:
            logger.warning("Re-opening after failure during half-open probe")
            self._reopened_from_half_open = True
            self._transition(CircuitState.OPEN)
            return

        if self._consecutive_failures >= self.failure_threshold:
            logger.warning(
                "Circuit breaker tripped after {} consecutive failures",
                self._consecutive_failures,
            )
            self._transition(CircuitState.OPEN)
```

### 步骤 3：自动 OPEN → HALF_OPEN 转换

`state` 属性检查恢复超时是否已过。这种惰性求值意味着
我们不需要后台定时器。

```python
    @property
    def state(self) -> CircuitState:
        """当前状态，超时后自动从 OPEN 转换为 HALF_OPEN。"""
        if self._state == CircuitState.OPEN:
            if self._reopened_from_half_open:
                # Reset recovery timer so the full timeout restarts from now.
                self._reopened_from_half_open = False
                self._last_failure_time = time.monotonic()
            else:
                elapsed = time.monotonic() - self._last_failure_time
                if elapsed >= self.recovery_timeout:
                    logger.info(
                        "Recovery timeout ({:.0f}s) elapsed — entering half-open",
                        self.recovery_timeout,
                    )
                    self._transition(CircuitState.HALF_OPEN)
        return self._state

    @property
    def can_execute(self) -> bool:
        """当熔断器允许请求通过时返回 True。"""
        current = self.state          # 可能触发 OPEN -> HALF_OPEN 转换
        if current == CircuitState.CLOSED:
            return True
        if current == CircuitState.HALF_OPEN:
            return self._half_open_calls < self.half_open_max_calls
        return False                  # OPEN

    def _transition(self, new_state: CircuitState) -> None:
        old = self._state
        self._state = new_state
        if new_state == CircuitState.OPEN:
            self._last_failure_time = time.monotonic()
        if new_state == CircuitState.HALF_OPEN:
            self._half_open_calls = 0
        if new_state == CircuitState.CLOSED:
            self._consecutive_failures = 0
        logger.debug("Circuit: {} -> {}", old.value, new_state.value)
```

### 步骤 4：提供者与模型的一对多映射 + 动态自定义提供者

在深入 `ProviderManager` 之前，我们先在配置层解决两个关键问题：
1. **一个提供者可以服务多个模型** — 在 `ProviderConfig` 中新增 `models` 字段。
2. **用户可以添加任意自定义提供者** — `ProvidersConfig` 使用 `extra="allow"`，
   这样 OpenRouter、百炼等只需写入 `config.json`，无需修改 Python 代码。

修改 `ultrabot/config/schema.py`：

```python
class ProviderConfig(Base):
    """单个 LLM 提供者的配置。"""
    api_key: str | None = Field(default=None, description="API key (prefer env vars).")
    api_base: str | None = Field(default=None, description="Base URL override.")
    enabled: bool = Field(default=True, description="Whether this provider is active.")
    priority: int = Field(default=100, description="Failover priority (lower = first).")
    models: list[str] = Field(default_factory=list, description="Model IDs this provider serves.")


class ProvidersConfig(Base):
    """所有提供者插槽。

    内置提供者为显式字段；任意自定义提供者通过 extra="allow" 支持，
    只需在 config.json 中添加新的键即可，无需修改代码。
    """
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="allow",
    )

    # 内置提供者
    openai: ProviderConfig = Field(default_factory=ProviderConfig)
    openai_compatible: ProviderConfig = Field(default_factory=ProviderConfig)
    anthropic: ProviderConfig = Field(default_factory=ProviderConfig)
    deepseek: ProviderConfig = Field(default_factory=ProviderConfig)
    groq: ProviderConfig = Field(default_factory=ProviderConfig)
    ollama: ProviderConfig = Field(
        default_factory=lambda: ProviderConfig(api_base="http://localhost:11434/v1")
    )

    def all_providers(self) -> dict[str, ProviderConfig]:
        """返回所有提供者（内置字段 + 自定义 extra）的名称→配置映射。"""
        result: dict[str, ProviderConfig] = {}
        # 内置字段
        for name in type(self).model_fields:
            result[name] = getattr(self, name)
        # 自定义提供者（通过 extra="allow" 接受的任意键）
        for name, val in (self.__pydantic_extra__ or {}).items():
            if isinstance(val, ProviderConfig):
                result[name] = val
            elif isinstance(val, dict):
                result[name] = ProviderConfig.model_validate(val)
        return result
```

这样配置文件就可以显式声明每个提供者支持的模型，
也可以添加 **任意自定义提供者**（如 OpenRouter、百炼）：

```json
{
  "providers": {
    "openaiCompatible": {
      "apiKey": "sk-...",
      "apiBase": "https://ark.cn-beijing.volces.com/api/coding/v3",
      "priority": 1,
      "models": ["minimax-m2.5", "deepseek-chat-v3"]
    },
    "anthropic": {
      "apiKey": "sk-ant-...",
      "priority": 2,
      "models": ["claude-3-opus", "claude-3-sonnet"]
    },
    "openrouter": {
      "apiKey": "sk-or-...",
      "apiBase": "https://openrouter.ai/api/v1",
      "priority": 10,
      "models": ["openai/gpt-4o", "anthropic/claude-3-opus"]
    },
    "bailian": {
      "apiKey": "sk-bailian-...",
      "apiBase": "https://dashscope.aliyuncs.com/compatible-mode/v1",
      "priority": 20,
      "models": ["qwen-max", "qwen-plus"]
    },
    "volcengine": {
      "apiKey": "sk-volc-...",
      "apiBase": "https://ark.cn-beijing.volces.com/api/v3",
      "priority": 30,
      "models": ["doubao-pro-256k"]
    }
  }
}
```

`Config.get_provider()` 通过 `all_providers()` 搜索所有提供者（内置 + 自定义）：

```python
    def get_provider(self, model: str | None = None) -> str:
        """从模型字符串中解析出提供者名称。

        解析优先级：
        1. 精确匹配 — 检查每个提供者的 models 列表。
        2. 关键字匹配 — 使用注册表中的 keywords 进行模糊匹配。
        3. 默认值 — 返回 agents.defaults.provider。
        """
        if model is None:
            return self.agents.defaults.provider

        model_lower = model.lower()
        all_provs = self.providers.all_providers()

        # 1. 精确匹配 — 检查每个提供者配置中声明的 models 列表
        for name, prov in all_provs.items():
            if prov.enabled and model in prov.models:
                return name

        # 2. 关键字匹配 — 使用注册表中的 keywords 进行模糊匹配
        from ultrabot.providers.registry import PROVIDERS as _SPECS
        for spec in _SPECS:
            prov_cfg = all_provs.get(spec.name)
            if prov_cfg and prov_cfg.enabled:
                for kw in spec.keywords:
                    if kw in model_lower:
                        return spec.name

        return self.agents.defaults.provider
```

### 步骤 5：ProviderManager

`ProviderManager` 将每个已注册的提供者包装在一个 `CircuitBreaker` 中，
并通过它们路由请求，实现自动故障转移。

创建 `ultrabot/providers/manager.py`：

```python
"""提供者编排 — 故障转移、熔断器集成。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

from loguru import logger

from ultrabot.providers.base import LLMProvider, LLMResponse
from ultrabot.providers.circuit_breaker import CircuitBreaker, CircuitState
from ultrabot.providers.registry import ProviderSpec, PROVIDERS, find_by_name, find_by_keyword


@dataclass
class _ProviderEntry:
    """一个已注册的提供者及其熔断器。"""
    name: str
    provider: LLMProvider
    breaker: CircuitBreaker
    spec: ProviderSpec | None = None
    models: list[str] = field(default_factory=list)


class ProviderManager:
    """所有已配置 LLM 提供者的中央编排器。"""

    def __init__(self, config: Any) -> None:
        self._config = config
        self._entries: dict[str, _ProviderEntry] = {}
        self._model_index: dict[str, str] = {}   # 模型 -> 提供者名称
        self._register_from_config(config)

    # -- 注册 --

    def _register_from_config(self, config: Any) -> None:
        """从 Config 对象中读取已启用的提供者并注册。

        遍历 config.providers.all_providers()（内置 + 自定义），
        通过注册表查找 spec；未注册的自定义提供者默认使用 OpenAI 兼容后端。
        """
        from ultrabot.config.schema import ProviderConfig
        from ultrabot.providers.anthropic_provider import AnthropicProvider
        from ultrabot.providers.openai_compat import OpenAICompatProvider

        providers_cfg = getattr(config, "providers", None)
        if providers_cfg is None:
            return

        # 收集已启用的提供者，按 priority 排序
        entries: list[tuple[str, ProviderConfig, ProviderSpec | None]] = []
        for name, prov_cfg in providers_cfg.all_providers().items():
            if not prov_cfg.enabled:
                continue
            spec = find_by_name(name)  # 可能为 None（纯自定义提供者）
            entries.append((name, prov_cfg, spec))

        entries.sort(key=lambda t: t[1].priority)

        for name, prov_cfg, spec in entries:
            api_key = prov_cfg.api_key or config.get_api_key(name)
            api_base = prov_cfg.api_base or (spec.default_api_base if spec else None)

            # 实例化对应的提供者
            if spec and spec.backend == "anthropic":
                provider: LLMProvider = AnthropicProvider(
                    api_key=api_key, api_base=api_base,
                )
            else:
                provider = OpenAICompatProvider(
                    api_key=api_key, api_base=api_base,
                )

            models = list(prov_cfg.models)
            entry = _ProviderEntry(
                name=name,
                provider=provider,
                breaker=CircuitBreaker(),
                spec=spec,
                models=models,
            )
            self._entries[name] = entry

            # 建立 model -> provider 索引
            for model_id in models:
                if model_id not in self._model_index:
                    self._model_index[model_id] = name

        if self._entries:
            logger.info("Registered {} provider(s): {}", len(self._entries),
                        ", ".join(self._entries))
        if self._model_index:
            logger.debug("Model index: {}", dict(self._model_index))
```

### 步骤 6：带故障转移的路由

这是管理器的核心。它为请求的模型构建一个按优先级排序的提供者列表，
按顺序逐个尝试，并在对应的熔断器上记录成功/失败。

```python
    async def chat_with_failover(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        stream: bool = False,
        on_content_delta: Callable | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """尝试主要提供者，失败时依次回退到健康的替代方案。"""
        model = model or getattr(self._config, "default_model", "gpt-4o")

        tried: set[str] = set()
        entries = self._ordered_entries(model)
        last_exc: Exception | None = None

        for entry in entries:
            if entry.name in tried:
                continue
            tried.add(entry.name)

            if not entry.breaker.can_execute:
                logger.debug("Skipping '{}' — breaker is {}", entry.name,
                             entry.breaker.state.value)
                continue

            try:
                if stream and on_content_delta:
                    resp = await entry.provider.chat_stream_with_retry(
                        messages=messages, tools=tools, model=model,
                        on_content_delta=on_content_delta, **kwargs,
                    )
                else:
                    resp = await entry.provider.chat_with_retry(
                        messages=messages, tools=tools, model=model, **kwargs,
                    )
                entry.breaker.record_success()    # 健康！
                return resp

            except Exception as exc:
                last_exc = exc
                entry.breaker.record_failure()    # 记录失败
                logger.warning(
                    "Provider '{}' failed: {}. Trying next.", entry.name, exc
                )

        raise RuntimeError(
            f"All providers exhausted for model '{model}'"
        ) from last_exc
```

### 步骤 7：优先级排序

```python
    def _ordered_entries(self, model: str) -> list[_ProviderEntry]:
        """返回排序后的条目：主要提供者优先，然后是关键字匹配的，最后是其余的。"""
        primary_name = self._model_index.get(model)
        result: list[_ProviderEntry] = []

        # 1. 该模型的主要提供者。
        if primary_name and primary_name in self._entries:
            result.append(self._entries[primary_name])

        # 2. 关键字匹配的提供者。
        for entry in self._entries.values():
            if entry.name == primary_name:
                continue
            if entry.spec:
                for kw in entry.spec.keywords:
                    if kw in model.lower():
                        result.append(entry)
                        break

        # 3. 其余所有提供者。
        for entry in self._entries.values():
            if entry not in result:
                result.append(entry)

        return result

    def health_check(self) -> dict[str, bool]:
        """提供者健康状态（熔断器状态）的快照。"""
        return {name: e.breaker.can_execute for name, e in self._entries.items()}
```

### 测试

```python
# tests/test_session10.py
"""课程 10 测试：熔断器 + 提供者-模型映射。"""
import time

import pytest

from ultrabot.config.schema import Config, ProviderConfig
from ultrabot.providers.circuit_breaker import CircuitBreaker, CircuitState


# ── 熔断器 ──────────────────────────────────────────────────


def test_breaker_starts_closed():
    cb = CircuitBreaker(failure_threshold=3)
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute is True


def test_breaker_trips_after_threshold():
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=1.0)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.CLOSED   # 还没有
    cb.record_failure()
    assert cb.state == CircuitState.OPEN     # 已熔断！
    assert cb.can_execute is False


def test_breaker_recovers_after_timeout():
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1)
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    time.sleep(0.15)
    assert cb.state == CircuitState.HALF_OPEN
    assert cb.can_execute is True


def test_half_open_success_closes():
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.0)
    cb.record_failure()                      # CLOSED -> OPEN
    _ = cb.state                             # OPEN -> HALF_OPEN (timeout=0)
    cb.record_success()
    assert cb.state == CircuitState.CLOSED


def test_half_open_failure_reopens():
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.0)
    cb.record_failure()
    _ = cb.state                             # -> HALF_OPEN
    cb.record_failure()
    assert cb.state == CircuitState.OPEN


# ── 提供者-模型映射：精确匹配 ────────────────────────────────


def test_explicit_model_list_matches_first():
    """当模型在 ProviderConfig.models 中声明时，精确匹配优先。"""
    cfg = Config(
        providers={
            "openai_compatible": {"models": ["my-custom-model", "gpt-4o"], "priority": 1},
            "deepseek": {"models": ["deepseek-chat"], "priority": 2},
        }
    )
    assert cfg.get_provider("my-custom-model") == "openai_compatible"
    assert cfg.get_provider("deepseek-chat") == "deepseek"


def test_one_provider_multiple_models():
    """一个提供者可以服务多个模型。"""
    cfg = Config(
        providers={
            "openai_compatible": {
                "models": ["minimax-m2.5", "gpt-4o", "custom-finetune-v2"],
                "priority": 1,
            },
        }
    )
    for model in ["minimax-m2.5", "gpt-4o", "custom-finetune-v2"]:
        assert cfg.get_provider(model) == "openai_compatible"


# ── 提供者-模型映射：关键字回退 ──────────────────────────────


def test_keyword_fallback_when_no_explicit_models():
    """未在 models 列表中的模型，回退到注册表关键字匹配。"""
    cfg = Config()  # 默认配置，无显式 models
    assert cfg.get_provider("gpt-4o-mini") == "openai_compatible"
    assert cfg.get_provider("claude-3-opus") == "anthropic"
    assert cfg.get_provider("deepseek-chat") == "deepseek"


def test_explicit_overrides_keyword():
    """精确匹配优先于关键字匹配。"""
    cfg = Config(
        providers={
            "openai_compatible": {"models": ["deepseek-chat"], "priority": 1},
            "deepseek": {"enabled": True, "priority": 2},
        }
    )
    assert cfg.get_provider("deepseek-chat") == "openai_compatible"


# ── 提供者-模型映射：默认回退 ────────────────────────────────


def test_unknown_model_returns_default():
    """无法匹配的模型返回默认提供者。"""
    cfg = Config()
    assert cfg.get_provider("some-unknown-model-xyz") == cfg.agents.defaults.provider


def test_none_model_returns_default():
    """model=None 返回默认提供者。"""
    cfg = Config()
    assert cfg.get_provider(None) == cfg.agents.defaults.provider


# ── ProviderConfig.models 字段 ───────────────────────────────


def test_provider_config_models_default_empty():
    """ProviderConfig.models 默认为空列表。"""
    pc = ProviderConfig()
    assert pc.models == []


def test_provider_config_models_from_json():
    """ProviderConfig 可以从 JSON（camelCase）加载 models。"""
    pc = ProviderConfig.model_validate({"models": ["a", "b", "c"]})
    assert pc.models == ["a", "b", "c"]


# ── 自定义提供者（extra="allow"）────────────────────────────


def test_custom_provider_via_extra():
    """通过 config.json 添加自定义提供者，无需修改代码。"""
    cfg = Config(
        providers={
            "openrouter": {
                "api_key": "sk-or-test",
                "api_base": "https://openrouter.ai/api/v1",
                "models": ["openai/gpt-4o", "anthropic/claude-3-opus"],
                "priority": 10,
            },
        }
    )
    # 精确匹配自定义提供者
    assert cfg.get_provider("openai/gpt-4o") == "openrouter"
    assert cfg.get_provider("anthropic/claude-3-opus") == "openrouter"


def test_custom_provider_in_all_providers():
    """all_providers() 包含内置和自定义提供者。"""
    cfg = Config(
        providers={
            "bailian": {
                "api_key": "sk-bailian",
                "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "models": ["qwen-max"],
            },
            "my_private_llm": {
                "api_key": "secret",
                "api_base": "https://internal.example.com/v1",
                "models": ["my-finetune-v3"],
            },
        }
    )
    all_provs = cfg.providers.all_providers()
    # 内置提供者仍在
    assert "openai" in all_provs
    assert "anthropic" in all_provs
    # 自定义提供者也在
    assert "bailian" in all_provs
    assert "my_private_llm" in all_provs
    assert all_provs["bailian"].api_base == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert all_provs["my_private_llm"].models == ["my-finetune-v3"]


def test_custom_provider_keyword_fallback():
    """注册表中有 spec 的自定义提供者支持关键字匹配。"""
    cfg = Config(
        providers={
            "bailian": {
                "api_key": "sk-test",
                "enabled": True,
            },
        }
    )
    # "qwen" 是 bailian 的注册关键字
    assert cfg.get_provider("qwen-max") == "bailian"


def test_multiple_custom_providers_failover_order():
    """多个自定义提供者按 priority 排序。"""
    cfg = Config(
        providers={
            "openrouter": {
                "api_key": "sk-or",
                "api_base": "https://openrouter.ai/api/v1",
                "priority": 20,
                "models": ["gpt-4o"],
            },
            "volcengine": {
                "api_key": "sk-volc",
                "api_base": "https://ark.cn-beijing.volces.com/api/v3",
                "priority": 10,
                "models": ["gpt-4o"],
            },
        }
    )
    from ultrabot.providers.manager import ProviderManager
    mgr = ProviderManager(cfg)
    # volcengine (priority=10) 排在 openrouter (priority=20) 前面
    ordered = mgr._ordered_entries("gpt-4o")
    prio_names = [e.name for e in ordered if e.name in ("volcengine", "openrouter")]
    assert prio_names[0] == "volcengine"
    assert prio_names[1] == "openrouter"
```

### 检查点

```bash
python -m pytest tests/test_session10.py -v
```

预期结果：全部 17 个测试通过（5 个熔断器 + 8 个提供者-模型映射 + 4 个自定义提供者）。

### 步骤 8：故障转移实战演示

单元测试验证了每个组件的正确性，但想要 **亲眼看到** 熔断器跳闸和故障转移，
需要一个端到端的演示脚本。

核心思路：用 `ControllableProvider` 替换真实的 `chat()` 方法，
通过 `healthy` 开关模拟宕机/恢复；同时把 `_DEFAULT_DELAYS` 设为 0 跳过重试等待。
无需真实 API key，整个演示约 2 秒完成。

创建 `scripts/demo_failover.py`：

```python
# /// script
# requires-python = ">=3.12"
# dependencies = ["loguru", "pydantic", "pydantic-settings", "openai"]
# ///
"""
故障转移演示脚本 — 模拟提供者宕机 + 自动故障转移。

用法:
    cd 10/
    uv run scripts/demo_failover.py

无需真实 API key，所有 LLM 调用均被 mock 替换。
脚本演示以下场景:

  场景 1  主提供者正常 → 请求直接走主提供者
  场景 2  主提供者连续失败 → 熔断器跳闸 → 自动切换到备用提供者
  场景 3  恢复超时过后 → 半开探测成功 → 熔断器闭合 → 主提供者恢复服务
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

# 让 import ultrabot 能找到 src
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from ultrabot.config.schema import Config
from ultrabot.providers.base import LLMResponse
from ultrabot.providers.circuit_breaker import CircuitBreaker
from ultrabot.providers.manager import ProviderManager

# ── 日志配置 ──────────────────────────────────────────────────

logger.remove()
logger.add(
    sys.stderr,
    format=(
        "<green>{time:HH:mm:ss.SSS}</green> | "
        "<level>{level:<8}</level> | "
        "<cyan>{message}</cyan>"
    ),
    level="DEBUG",
)


# ── 构造两个提供者的配置 ─────────────────────────────────────

def make_config() -> Config:
    """创建带有主（volcengine）+ 备（openrouter）的配置。"""
    return Config(
        providers={
            "volcengine": {
                "api_key": "fake-volc-key",
                "api_base": "https://ark.cn-beijing.volces.com/api/v3",
                "priority": 1,
                "models": ["doubao-pro-256k"],
            },
            "openrouter": {
                "api_key": "fake-or-key",
                "api_base": "https://openrouter.ai/api/v1",
                "priority": 10,
                "models": ["doubao-pro-256k", "gpt-4o"],
            },
        }
    )


# ── 可控的 mock chat ─────────────────────────────────────────

class ControllableProvider:
    """在真实 provider 上包一层，可以随时 开/关 来模拟宕机。"""

    def __init__(self, name: str, healthy: bool = True) -> None:
        self.name = name
        self.healthy = healthy

    async def chat(self, **kwargs) -> LLMResponse:
        if not self.healthy:
            raise ConnectionError(f"[{self.name}] Connection refused — 模拟宕机")
        return LLMResponse(
            content=f"[{self.name}] 你好！这是来自 {self.name} 的回复。",
            finish_reason="stop",
        )


# ── 辅助：打印健康状态 ───────────────────────────────────────

def print_health(mgr: ProviderManager) -> None:
    health = mgr.health_check()
    states = {
        name: mgr._entries[name].breaker.state.value
        for name in health
    }
    logger.info("健康状态: {}", states)


# ── 主演示 ────────────────────────────────────────────────────

async def main() -> None:
    cfg = make_config()
    mgr = ProviderManager(cfg)

    # 把熔断器阈值调低（方便演示），恢复超时设为 2 秒
    for entry in mgr._entries.values():
        entry.breaker.failure_threshold = 3
        entry.breaker.recovery_timeout = 2.0

    # 用 ControllableProvider 替换真实的 chat() 方法
    ctrl_volc = ControllableProvider("volcengine", healthy=True)
    ctrl_or   = ControllableProvider("openrouter", healthy=True)
    mgr._entries["volcengine"].provider.chat = ctrl_volc.chat
    mgr._entries["openrouter"].provider.chat = ctrl_or.chat

    # 同时禁用 retry 层的 sleep（让演示秒过）
    for entry in mgr._entries.values():
        entry.provider._DEFAULT_DELAYS = (0.0, 0.0, 0.0)

    messages = [{"role": "user", "content": "你好"}]

    # ────────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("场景 1：主提供者（volcengine）正常")
    logger.info("=" * 60)

    for i in range(3):
        resp = await mgr.chat_with_failover(messages, model="doubao-pro-256k")
        logger.info("请求 {} → {}", i + 1, resp.content)
    print_health(mgr)

    # ────────────────────────────────────────────────────────
    logger.info("")
    logger.info("=" * 60)
    logger.info("场景 2：主提供者宕机 → 熔断器跳闸 → 自动故障转移")
    logger.info("=" * 60)

    ctrl_volc.healthy = False
    logger.warning(">>> 模拟 volcengine 宕机!")

    for i in range(5):
        try:
            resp = await mgr.chat_with_failover(messages, model="doubao-pro-256k")
            logger.info("请求 {} → {}", i + 1, resp.content)
        except RuntimeError as exc:
            logger.error("请求 {} → 全部提供者耗尽: {}", i + 1, exc)
        print_health(mgr)

    # ────────────────────────────────────────────────────────
    logger.info("")
    logger.info("=" * 60)
    logger.info("场景 3：等待恢复超时 → 半开探测 → 主提供者恢复")
    logger.info("=" * 60)

    ctrl_volc.healthy = True
    logger.info(">>> volcengine 已恢复! 等待 2 秒恢复超时...")
    await asyncio.sleep(2.1)

    for i in range(3):
        resp = await mgr.chat_with_failover(messages, model="doubao-pro-256k")
        logger.info("请求 {} → {}", i + 1, resp.content)
    print_health(mgr)

    # ────────────────────────────────────────────────────────
    logger.info("")
    logger.info("=" * 60)
    logger.info("演示完成!")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
```

运行演示：

```bash
cd 10/
uv run scripts/demo_failover.py
```

预期输出（关键行）：

```
场景 1：主提供者（volcengine）正常
请求 1 → [volcengine] 你好！这是来自 volcengine 的回复。
请求 2 → [volcengine] 你好！这是来自 volcengine 的回复。
请求 3 → [volcengine] 你好！这是来自 volcengine 的回复。
健康状态: {'volcengine': 'closed', 'openrouter': 'closed', ...}

场景 2：主提供者宕机 → 熔断器跳闸 → 自动故障转移
>>> 模拟 volcengine 宕机!
Provider 'volcengine' failed: Connection refused. Trying next.
请求 1 → [openrouter] 你好！这是来自 openrouter 的回复。     ← 自动回退
...
Circuit breaker tripped after 3 consecutive failures          ← 熔断器跳闸
请求 3 → [openrouter] 你好！这是来自 openrouter 的回复。
Skipping 'volcengine' — breaker is open                       ← 直接跳过
请求 4 → [openrouter] 你好！这是来自 openrouter 的回复。
健康状态: {'volcengine': 'open', 'openrouter': 'closed', ...}

场景 3：等待恢复超时 → 半开探测 → 主提供者恢复
>>> volcengine 已恢复! 等待 2 秒恢复超时...
Recovery timeout (2s) elapsed — entering half-open             ← 自动半开
Circuit breaker closing after successful probe                 ← 探测成功
请求 1 → [volcengine] 你好！这是来自 volcengine 的回复。      ← 主提供者恢复
健康状态: {'volcengine': 'closed', 'openrouter': 'closed', ...}
```

脚本要点：
- `ControllableProvider` 替换真实 `chat()` 方法，通过 `healthy` 开关模拟宕机/恢复
- `_DEFAULT_DELAYS = (0.0, 0.0, 0.0)` 跳过重试层的等待
- 熔断器阈值调低为 3，恢复超时设为 2 秒，让演示快速完成
- 无需真实 API key，全程 mock

### 本课成果

1. 一个 `CircuitBreaker`，跟踪连续失败并在
CLOSED → OPEN → HALF_OPEN → CLOSED 之间转换，防止级联故障。
2. `ProviderConfig.models` 字段，支持一对多的提供者-模型映射。
`Config.get_provider()` 先精确匹配 `models` 列表，再回退到注册表关键字。
3. `ProvidersConfig` 使用 `extra="allow"` 支持动态自定义提供者，
用户只需在 `config.json` 中添加新的提供者（如 OpenRouter、百炼、火山引擎），
`all_providers()` 方法统一返回内置和自定义提供者。
4. 注册表新增 `openrouter`、`bailian`、`volcengine` 的 `ProviderSpec`，
自定义提供者也可以通过注册表的关键字回退进行模型匹配。
5. 一个 `ProviderManager`，通过 `_register_from_config()` 从配置自动创建提供者实例
（遍历 `all_providers()` 而非仅注册表），将每个提供者包装在熔断器中，
当主要提供者宕机时自动故障转移到下一个健康的提供者。
6. 一个端到端的故障转移演示脚本（`scripts/demo_failover.py`），
通过可控 mock 展示三个场景：正常运行、宕机故障转移、恢复探测。

---
