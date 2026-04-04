# ultrabot/channels/qq.py
"""使用 botpy SDK 的 QQ Bot 通道。"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

from loguru import logger
from ultrabot.channels.base import BaseChannel

if TYPE_CHECKING:
    import botpy
    from ultrabot.bus.events import InboundMessage, OutboundMessage
    from ultrabot.bus.queue import MessageBus


def _make_bot_class(channel: "QQChannel") -> "type[botpy.Client]":
    """创建绑定到给定通道的 botpy Client 子类。"""
    import botpy as _botpy

    intents = _botpy.Intents(public_messages=True, direct_message=True)

    class _Bot(_botpy.Client):
        def __init__(self):
            super().__init__(intents=intents, ext_handlers=False)

        async def on_ready(self):
            logger.info("QQ bot ready: {}", self.robot.name)

        async def on_c2c_message_create(self, message):
            await channel._on_message(message, is_group=False)

        async def on_group_at_message_create(self, message):
            await channel._on_message(message, is_group=True)

    return _Bot


class QQChannel(BaseChannel):
    """QQ Bot 通道 — C2C 和群消息。"""

    @property
    def name(self) -> str:
        return "qq"

    def __init__(self, config: dict, bus: "MessageBus") -> None:
        super().__init__(config, bus)
        self._app_id = config.get("appId", "")
        self._secret = config.get("secret", "")
        self._msg_format = config.get("msgFormat", "plain")  # 或 "markdown"
        self._chat_type_cache: dict[str, str] = {}
        self._client: Any = None

    async def start(self) -> None:
        self._running = True
        self._client = _make_bot_class(self)()
        await self._client.start(
            appid=self._app_id, secret=self._secret
        )

    async def send(self, msg: "OutboundMessage") -> None:
        """根据配置发送文本（纯文本或 markdown）。"""
        chat_type = self._chat_type_cache.get(msg.chat_id, "c2c")
        is_group = chat_type == "group"

        payload = {
            "msg_type": 2 if self._msg_format == "markdown" else 0,
            "content": msg.content if self._msg_format == "plain" else None,
            "markdown": {"content": msg.content}
                if self._msg_format == "markdown" else None,
        }

        if is_group:
            await self._client.api.post_group_message(
                group_openid=msg.chat_id, **payload
            )
        else:
            await self._client.api.post_c2c_message(
                openid=msg.chat_id, **payload
            )

    async def _on_message(self, message: Any, *, is_group: bool) -> None:
        """处理 QQ 消息并发布到消息总线。"""
        from ultrabot.bus.events import InboundMessage

        try:
            # 提取消息字段
            sender_id = getattr(message, "author", None)
            if sender_id and hasattr(sender_id, "id"):
                sender_id = str(sender_id.id)
            elif sender_id and hasattr(sender_id, "member_openid"):
                sender_id = str(sender_id.member_openid)
            else:
                sender_id = str(sender_id) if sender_id else ""

            if is_group:
                chat_id = getattr(message, "group_openid", "") or ""
                self._chat_type_cache[chat_id] = "group"
            else:
                chat_id = getattr(message, "author", None)
                if chat_id and hasattr(chat_id, "user_openid"):
                    chat_id = str(chat_id.user_openid)
                else:
                    chat_id = sender_id
                self._chat_type_cache[chat_id] = "c2c"

            content = getattr(message, "content", "") or ""
            content = content.strip()

            if not content:
                return

            logger.info("QQ {} message from {}: {}",
                        "group" if is_group else "c2c",
                        sender_id, content[:50])
            await self.bus.publish(InboundMessage(
                channel=self.name,
                sender_id=sender_id,
                chat_id=chat_id,
                content=content,
                metadata={"is_group": is_group},
            ))
        except Exception as exc:
            logger.error("QQ _on_message error: {}", exc)

    async def stop(self) -> None:
        """优雅关闭 QQ Bot 连接。"""
        self._running = False
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                logger.debug("QQ client close error (ignored)")
            self._client = None
        logger.info("QQChannel stopped")
