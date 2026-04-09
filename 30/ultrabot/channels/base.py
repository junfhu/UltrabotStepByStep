# ultrabot/channels/base.py
"""基础通道抽象和通道管理器。"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from ultrabot.bus.events import OutboundMessage
    from ultrabot.bus.queue import MessageBus


class BaseChannel(ABC):
    """所有消息通道的抽象基类。"""

    def __init__(self, config: dict, bus: "MessageBus") -> None:
        self.config = config
        self.bus = bus
        self._running = False

    @property
    @abstractmethod
    def name(self) -> str:
        """唯一标识符（例如 'telegram'、'discord'）。"""
        ...

    @abstractmethod
    async def start(self) -> None:
        """开始监听传入消息。"""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """优雅关闭。"""
        ...

    @abstractmethod
    async def send(self, message: "OutboundMessage") -> None:
        """向对应的聊天发送消息。"""
        ...

    async def send_with_retry(
        self,
        message: "OutboundMessage",
        max_retries: int = 3,
        base_delay: float = 1.0,
    ) -> None:
        """带指数退避的重试发送。"""
        last_exc: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                await self.send(message)
                return
            except Exception as exc:
                last_exc = exc
                if attempt < max_retries:
                    delay = base_delay * (2 ** (attempt - 1))
                    logger.warning(
                        "[{}] attempt {}/{} failed, retry in {:.1f}s: {}",
                        self.name, attempt, max_retries, delay, exc,
                    )
                    await asyncio.sleep(delay)
        logger.error("[{}] send failed after {} attempts", self.name, max_retries)
        raise last_exc  # type: ignore[misc]

    async def send_typing(self, chat_id: str | int) -> None:
        """发送输入指示器（默认为无操作）。"""


class ChannelManager:
    """消息通道的注册中心和生命周期管理器。"""

    def __init__(self, channels_config: dict, bus: "MessageBus") -> None:
        self.channels_config = channels_config
        self.bus = bus
        self._channels: dict[str, BaseChannel] = {}

    def register(self, channel: BaseChannel) -> None:
        self._channels[channel.name] = channel
        logger.info("Channel '{}' registered", channel.name)

    async def start_all(self) -> None:
        for name, channel in self._channels.items():
            ch_cfg = self.channels_config.get(name, {})
            if not ch_cfg.get("enabled", True):
                logger.info("Channel '{}' disabled — skipping", name)
                continue
            try:
                await channel.start()
                logger.info("Channel '{}' started", name)
            except Exception:
                logger.exception("Failed to start channel '{}'", name)

    async def stop_all(self) -> None:
        for name, channel in self._channels.items():
            try:
                await channel.stop()
            except Exception:
                logger.exception("Error stopping channel '{}'", name)

    def get_channel(self, name: str) -> BaseChannel | None:
        return self._channels.get(name)
