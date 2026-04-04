# ultrabot/providers/circuit_breaker.py
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
