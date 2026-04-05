# ultrabot/security/guard.py
"""安全执行 — 速率限制、输入清理、访问控制。"""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, field

from loguru import logger
from ultrabot.bus.events import InboundMessage


@dataclass
class SecurityConfig:
    """所有安全子系统的配置。

    Attributes:
        rpm:              每个发送者每分钟允许的请求数。
        burst:            在 rpm 之上的额外突发容量，用于短暂的峰值。
        max_input_length: 单条消息的最大字符数。
        blocked_patterns: 内容中不得出现的正则模式。
        allow_from:       逐通道的发送者 ID 允许列表。
                          ``"*"`` 表示允许所有发送者。
    """
    rpm: int = 30
    burst: int = 5
    max_input_length: int = 8192
    blocked_patterns: list[str] = field(default_factory=list)
    allow_from: dict[str, list[str]] = field(default_factory=dict)


class RateLimiter:
    """使用每个发送者一个双端队列的滑动窗口速率限制器。"""

    def __init__(self, rpm: int = 30, burst: int = 5) -> None:
        self.rpm = rpm
        self.burst = burst
        self._window = 60.0
        self._timestamps: dict[str, deque[float]] = {}

    async def acquire(self, sender_id: str) -> bool:
        """尝试消费一个令牌。允许则返回 True。"""
        now = time.monotonic()
        if sender_id not in self._timestamps:
            self._timestamps[sender_id] = deque()

        dq = self._timestamps[sender_id]

        # 清除窗口外的时间戳。
        while dq and (now - dq[0]) > self._window:
            dq.popleft()

        capacity = self.rpm + self.burst
        if len(dq) >= capacity:
            logger.warning("Rate limit exceeded for sender {}", sender_id)
            return False

        dq.append(now)
        return True


class InputSanitizer:
    """验证和清理原始消息内容。"""

    @staticmethod
    def validate_length(content: str, max_length: int) -> bool:
        return len(content) <= max_length

    @staticmethod
    def check_blocked_patterns(content: str, patterns: list[str]) -> str | None:
        """返回第一个匹配的模式，或 None。"""
        for pattern in patterns:
            try:
                if re.search(pattern, content, re.IGNORECASE):
                    return pattern
            except re.error:
                logger.error("Invalid blocked regex: {}", pattern)
        return None

    @staticmethod
    def sanitize(content: str) -> str:
        """剥除空字节和 ASCII 控制字符（保留制表符、换行符、回车符）。"""
        content = content.replace("\x00", "")
        content = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", "", content)
        return content


class AccessController:
    """基于通道的发送者允许列表。

    未在配置中列出的通道默认开放（等同于 ``"*"``）。
    """

    def __init__(self, allow_from: dict[str, list[str]] | None = None) -> None:
        self._allow_from = allow_from or {}

    def is_allowed(self, channel: str, sender_id: str) -> bool:
        allowed = self._allow_from.get(channel)
        if allowed is None:
            return True                  # 无规则 = 开放
        if "*" in allowed:
            return True
        return sender_id in allowed


class SecurityGuard:
    """统一的安全门面。"""

    def __init__(self, config: SecurityConfig | None = None) -> None:
        self.config = config or SecurityConfig()
        self.rate_limiter = RateLimiter(
            rpm=self.config.rpm, burst=self.config.burst
        )
        self.sanitizer = InputSanitizer()
        self.access_controller = AccessController(
            allow_from=self.config.allow_from
        )

    async def check_inbound(
        self, message: InboundMessage
    ) -> tuple[bool, str]:
        """根据所有安全策略进行验证。

        返回 (allowed, reason)。
        """
        # 1. 访问控制。
        if not self.access_controller.is_allowed(
            message.channel, message.sender_id
        ):
            reason = f"Access denied for {message.sender_id} on {message.channel}"
            logger.warning(reason)
            return False, reason

        # 2. 速率限制。
        if not await self.rate_limiter.acquire(message.sender_id):
            return False, f"Rate limit exceeded for {message.sender_id}"

        # 3. 输入长度。
        if not self.sanitizer.validate_length(
            message.content, self.config.max_input_length
        ):
            reason = (
                f"Input too long ({len(message.content)} chars, "
                f"max {self.config.max_input_length})"
            )
            return False, reason

        # 4. 阻止模式。
        matched = self.sanitizer.check_blocked_patterns(
            message.content, self.config.blocked_patterns,
        )
        if matched is not None:
            return False, f"Blocked pattern matched: {matched}"

        return True, "ok"
