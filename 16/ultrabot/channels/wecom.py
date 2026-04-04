# ultrabot/channels/wecom.py
"""使用 wecom_aibot_sdk WebSocket 长连接的企业微信通道。"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

from loguru import logger
from ultrabot.channels.base import BaseChannel

if TYPE_CHECKING:
    from ultrabot.bus.events import OutboundMessage
    from ultrabot.bus.queue import MessageBus

import importlib.util
_WECOM_AVAILABLE = importlib.util.find_spec("wecom_aibot_sdk") is not None


class WecomChannel(BaseChannel):
    """使用 WebSocket 长连接的企业微信通道。"""

    @property
    def name(self) -> str:
        return "wecom"

    def __init__(self, config: dict, bus: "MessageBus") -> None:
        super().__init__(config, bus)
        self._bot_id: str = config.get("botId", "")
        self._secret: str = config.get("secret", "")
        self._allow_from: list[str] = config.get("allowFrom", [])
        self._welcome_message: str = config.get("welcomeMessage", "")
        self._client: Any = None
        self._processed_ids: OrderedDict[str, None] = OrderedDict()
        self._chat_frames: dict[str, Any] = {}   # 用于回复路由

    async def start(self) -> None:
        from wecom_aibot_sdk import WSClient, generate_req_id

        self._generate_req_id = generate_req_id
        self._client = WSClient({
            "bot_id": self._bot_id,
            "secret": self._secret,
            "reconnect_interval": 1000,
            "max_reconnect_attempts": -1,
            "heartbeat_interval": 30000,
        })

        # 注册事件处理器。
        self._client.on("message.text", self._on_text_message)
        self._client.on("event.enter_chat", self._on_enter_chat)
        # ... 图片、语音、文件、混合消息处理器 ...

        await self._client.connect_async()

    async def send(self, msg: "OutboundMessage") -> None:
        """使用流式回复 API 进行回复。"""
        frame = self._chat_frames.get(msg.chat_id)
        if not frame:
            logger.warning("No frame for chat {}", msg.chat_id)
            return
        stream_id = self._generate_req_id("stream")
        await self._client.reply_stream(
            frame, stream_id, msg.content.strip(), finish=True
        )
