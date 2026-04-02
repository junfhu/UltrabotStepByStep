# 课程 10：熔断器 + 提供者故障转移

**目标：** 通过为每个提供者添加熔断器并自动故障转移到健康的替代方案，保护智能体免受级联 LLM 故障的影响。

**你将学到：**
- 熔断器状态机模式（CLOSED → OPEN → HALF_OPEN）
- 可配置阈值的失败计数
- 使用 `time.monotonic()` 的基于时间的恢复
- 通过熔断器路由请求的 `ProviderManager`
- 基于优先级的故障转移链

**新建文件：**
- `ultrabot/providers/circuit_breaker.py` — `CircuitState` 枚举 + `CircuitBreaker`
- `ultrabot/providers/manager.py` — `ProviderManager` 编排器

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
        self._last_failure_time = time.monotonic()

        if self._state == CircuitState.HALF_OPEN:
            logger.warning("Re-opening after failure during half-open probe")
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
        if new_state == CircuitState.HALF_OPEN:
            self._half_open_calls = 0
        if new_state == CircuitState.CLOSED:
            self._consecutive_failures = 0
        logger.debug("Circuit: {} -> {}", old.value, new_state.value)
```

### 步骤 4：ProviderManager

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
from ultrabot.providers.registry import ProviderSpec, find_by_name, find_by_keyword


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
```

### 步骤 5：带故障转移的路由

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

### 步骤 6：优先级排序

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
# tests/test_circuit_breaker.py
import time
from ultrabot.providers.circuit_breaker import CircuitBreaker, CircuitState


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
```

### 检查点

```bash
python -m pytest tests/test_circuit_breaker.py -v
```

预期结果：全部 5 个测试通过。要实际查看故障转移效果，可以在 `ultrabot.yaml` 中配置两个提供者，
关闭主要提供者的 API，然后观察日志：

```
WARNING  Provider 'openai' failed: Connection refused. Trying next.
INFO     Falling back to provider 'ollama' for model 'gpt-4o'
```

### 本课成果

一个 `CircuitBreaker`，跟踪连续失败并在
CLOSED → OPEN → HALF_OPEN → CLOSED 之间转换，防止级联故障。一个
`ProviderManager` 将每个提供者包装在熔断器中，当主要提供者宕机时
自动故障转移到下一个健康的提供者。

---
