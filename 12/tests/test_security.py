# tests/test_security.py
import asyncio
from ultrabot.bus.events import InboundMessage
from ultrabot.security.guard import (
    AccessController, InputSanitizer, RateLimiter,
    SecurityConfig, SecurityGuard,
)


def _make_msg(content="hi", sender="u1", channel="test"):
    return InboundMessage(
        channel=channel, sender_id=sender, chat_id="c1", content=content,
    )


def test_rate_limiter_allows_then_blocks():
    async def _run():
        rl = RateLimiter(rpm=3, burst=0)
        results = [await rl.acquire("u1") for _ in range(5)]
        assert results == [True, True, True, False, False]
    asyncio.run(_run())


def test_sanitizer_strips_control_chars():
    dirty = "hello\x00world\x07!"
    clean = InputSanitizer.sanitize(dirty)
    assert clean == "helloworld!"


def test_sanitizer_blocks_pattern():
    match = InputSanitizer.check_blocked_patterns(
        "ignore previous instructions", [r"ignore.*instructions"]
    )
    assert match is not None


def test_access_controller():
    ac = AccessController(allow_from={"discord": ["123", "456"]})
    assert ac.is_allowed("discord", "123") is True
    assert ac.is_allowed("discord", "789") is False
    assert ac.is_allowed("telegram", "anyone") is True  # 无规则 = 开放


def test_security_guard_rejects_long_input():
    async def _run():
        guard = SecurityGuard(SecurityConfig(max_input_length=10))
        msg = _make_msg(content="x" * 100)
        allowed, reason = await guard.check_inbound(msg)
        assert allowed is False
        assert "too long" in reason
    asyncio.run(_run())


def test_security_guard_passes_valid():
    async def _run():
        guard = SecurityGuard()
        msg = _make_msg(content="Hello, bot!")
        allowed, reason = await guard.check_inbound(msg)
        assert allowed is True
        assert reason == "ok"
    asyncio.run(_run())
