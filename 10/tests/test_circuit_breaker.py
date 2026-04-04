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
