# ultrabot/bus/events.py
"""消息总线上入站和出站消息的数据类定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class InboundMessage:
    """从任何通道接收的、进入处理管道的消息。

    ``priority`` 字段控制处理顺序：数字越大
    越先被处理（类似 VIP 通道）。
    """

    channel: str                          # 例如 "telegram"、"discord"
    sender_id: str                        # 唯一发送者标识
    chat_id: str                          # 对话标识
    content: str                          # 原始文本内容
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    media: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    session_key_override: str | None = None
    priority: int = 0                     # 0 = 普通；数值越高 = 越快

    @property
    def session_key(self) -> str:
        """推导会话密钥：使用覆盖值或 ``{channel}:{chat_id}``。"""
        if self.session_key_override is not None:
            return self.session_key_override
        return f"{self.channel}:{self.chat_id}"

    def __lt__(self, other: InboundMessage) -> bool:
        """高优先级在最小堆中被视为"小于"。

        ``asyncio.PriorityQueue`` 是最小堆，所以我们反转比较：
        priority=10 的消息"小于" priority=0 的消息，
        从而使其优先出队。
        """
        if not isinstance(other, InboundMessage):
            return NotImplemented
        return self.priority > other.priority


@dataclass
class OutboundMessage:
    """要通过通道适配器发送出去的消息。"""

    channel: str
    chat_id: str
    content: str
    reply_to: str | None = None
    media: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
