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

        self._running = True
        await self._client.connect_async()

    async def stop(self) -> None:
        """优雅关闭 WebSocket 连接。"""
        self._running = False
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:
                logger.debug("Wecom client disconnect error (ignored)")
            self._client = None
        logger.info("WecomChannel stopped")

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

    def _is_allowed(self, sender_id: str) -> bool:
        """检查发送者是否在允许列表中（空列表表示允许所有人）。"""
        if not self._allow_from:
            return True
        return sender_id in self._allow_from

    async def _on_text_message(self, frame: Any) -> None:
        """处理收到的文本消息回调。"""
        from ultrabot.bus.events import InboundMessage

        msg_id = getattr(frame, "msg_id", None) or str(id(frame))
        # 消息去重
        if msg_id in self._processed_ids:
            return
        self._processed_ids[msg_id] = None
        while len(self._processed_ids) > 1000:
            self._processed_ids.popitem(last=False)

        sender_id = getattr(frame, "sender_id", "") or ""
        chat_id = getattr(frame, "chat_id", "") or sender_id
        content = getattr(frame, "content", "") or ""

        if not self._is_allowed(sender_id):
            logger.debug("Wecom message from {} blocked by allowFrom", sender_id)
            return

        # 保存 frame 用于回复路由
        self._chat_frames[chat_id] = frame

        logger.info("Wecom text from {} in {}: {}", sender_id, chat_id, content[:50])
        await self.bus.publish(InboundMessage(
            channel=self.name,
            sender_id=sender_id,
            chat_id=chat_id,
            content=content,
        ))

    async def _on_enter_chat(self, frame: Any) -> None:
        """处理用户进入聊天事件 — 发送欢迎消息。"""
        chat_id = getattr(frame, "chat_id", "") or ""
        self._chat_frames[chat_id] = frame

        if self._welcome_message:
            logger.info("Wecom enter_chat event for {}, sending welcome", chat_id)
            try:
                stream_id = self._generate_req_id("stream")
                await self._client.reply_stream(
                    frame, stream_id, self._welcome_message, finish=True
                )
            except Exception as exc:
                logger.error("Failed to send welcome message: {}", exc)
