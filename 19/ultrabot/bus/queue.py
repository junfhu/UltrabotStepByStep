# ultrabot/bus/queue.py
"""基于优先级的异步消息总线。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

from loguru import logger
from ultrabot.bus.events import InboundMessage, OutboundMessage

InboundHandler = Callable[
    [InboundMessage], Coroutine[Any, Any, OutboundMessage | None]
]
OutboundSubscriber = Callable[
    [OutboundMessage], Coroutine[Any, Any, None]
]


class MessageBus:
    """带有优先级入站队列和扇出出站分发的中央总线。"""

    def __init__(self, max_retries: int = 3, queue_maxsize: int = 0) -> None:
        self.max_retries = max_retries
        self._inbound_queue: asyncio.PriorityQueue[InboundMessage] = (
            asyncio.PriorityQueue(maxsize=queue_maxsize)
        )
        self._inbound_handler: InboundHandler | None = None
        self._outbound_subscribers: list[OutboundSubscriber] = []
        self.dead_letter_queue: list[InboundMessage] = []
        self._shutdown_event = asyncio.Event()

    async def publish(self, message: InboundMessage) -> None:
        await self._inbound_queue.put(message)
        logger.debug(
            "Published | channel={} chat_id={} priority={}",
            message.channel, message.chat_id, message.priority,
        )

    def set_inbound_handler(self, handler: InboundHandler) -> None:
        self._inbound_handler = handler

    async def dispatch_inbound(self) -> None:
        logger.info("Inbound dispatch loop started")
        while not self._shutdown_event.is_set():
            try:
                message = await asyncio.wait_for(
                    self._inbound_queue.get(), timeout=1.0,
                )
            except asyncio.TimeoutError:
                continue
            if self._inbound_handler is None:
                logger.warning("No handler registered — message dropped")
                self._inbound_queue.task_done()
                continue
            await self._process_with_retries(message)
            self._inbound_queue.task_done()
        logger.info("Inbound dispatch loop stopped")

    async def _process_with_retries(self, message: InboundMessage) -> None:
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
        self.dead_letter_queue.append(message)
        logger.error(
            "Dead-lettered after {} retries | session_key={}",
            self.max_retries, message.session_key,
        )

    def subscribe(self, handler: OutboundSubscriber) -> None:
        self._outbound_subscribers.append(handler)

    async def send_outbound(self, message: OutboundMessage) -> None:
        for subscriber in self._outbound_subscribers:
            try:
                await subscriber(message)
            except Exception:
                logger.exception("Outbound subscriber failed")

    def shutdown(self) -> None:
        self._shutdown_event.set()

    @property
    def inbound_queue_size(self) -> int:
        return self._inbound_queue.qsize()

    @property
    def dead_letter_count(self) -> int:
        return len(self.dead_letter_queue)
