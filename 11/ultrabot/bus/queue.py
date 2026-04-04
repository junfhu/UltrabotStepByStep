# ultrabot/bus/queue.py
"""基于优先级的异步消息总线。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

from loguru import logger
from ultrabot.bus.events import InboundMessage, OutboundMessage

# 处理器签名的类型别名。
InboundHandler = Callable[
    [InboundMessage], Coroutine[Any, Any, OutboundMessage | None]
]
OutboundSubscriber = Callable[
    [OutboundMessage], Coroutine[Any, Any, None]
]


class MessageBus:
    """带有优先级入站队列和扇出出站分发的中央总线。

    Parameters:
        max_retries:   发送到死信队列之前的尝试次数。
        queue_maxsize: 入站队列的上限（0 = 无限制）。
    """

    def __init__(self, max_retries: int = 3, queue_maxsize: int = 0) -> None:
        self.max_retries = max_retries

        # 入站优先级队列 — 排序使用 InboundMessage.__lt__。
        self._inbound_queue: asyncio.PriorityQueue[InboundMessage] = (
            asyncio.PriorityQueue(maxsize=queue_maxsize)
        )
        self._inbound_handler: InboundHandler | None = None
        self._outbound_subscribers: list[OutboundSubscriber] = []
        self.dead_letter_queue: list[InboundMessage] = []
        self._shutdown_event = asyncio.Event()

    async def publish(self, message: InboundMessage) -> None:
        """将入站消息加入队列等待处理。"""
        await self._inbound_queue.put(message)
        logger.debug(
            "Published | channel={} chat_id={} priority={}",
            message.channel, message.chat_id, message.priority,
        )

    def set_inbound_handler(self, handler: InboundHandler) -> None:
        """注册处理每条入站消息的处理器。"""
        self._inbound_handler = handler

    async def dispatch_inbound(self) -> None:
        """长期运行的循环：拉取消息并处理。

        运行直到 shutdown() 被调用。失败的消息会被重试
        最多 max_retries 次；之后进入 dead_letter_queue。
        """
        logger.info("Inbound dispatch loop started")

        while not self._shutdown_event.is_set():
            try:
                message = await asyncio.wait_for(
                    self._inbound_queue.get(), timeout=1.0,
                )
            except asyncio.TimeoutError:
                continue                          # 检查关闭标志

            if self._inbound_handler is None:
                logger.warning("No handler registered — message dropped")
                self._inbound_queue.task_done()
                continue

            await self._process_with_retries(message)
            self._inbound_queue.task_done()

        logger.info("Inbound dispatch loop stopped")

    async def _process_with_retries(self, message: InboundMessage) -> None:
        """带重试的处理尝试；重试耗尽后进入死信队列。"""
        for attempt in range(1, self.max_retries + 1):
            try:
                result = await self._inbound_handler(message)
                if result is not None:
                    await self.send_outbound(result)
                return
            except Exception:
                logger.exception(
                    "Error processing (attempt {}/{}) | session_key={}",
                    attempt, self.max_retries, message.session_key,
                )
        # 所有重试已耗尽。
        self.dead_letter_queue.append(message)
        logger.error(
            "Dead-lettered after {} retries | session_key={}",
            self.max_retries, message.session_key,
        )

    def subscribe(self, handler: OutboundSubscriber) -> None:
        """注册一个出站订阅者。"""
        self._outbound_subscribers.append(handler)

    async def send_outbound(self, message: OutboundMessage) -> None:
        """扇出到所有已注册的出站订阅者。"""
        for subscriber in self._outbound_subscribers:
            try:
                await subscriber(message)
            except Exception:
                logger.exception("Outbound subscriber failed")

    def shutdown(self) -> None:
        """通知分发循环停止。"""
        self._shutdown_event.set()

    @property
    def inbound_queue_size(self) -> int:
        return self._inbound_queue.qsize()

    @property
    def dead_letter_count(self) -> int:
        return len(self.dead_letter_queue)
