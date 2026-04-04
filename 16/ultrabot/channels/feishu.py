# ultrabot/channels/feishu.py
"""使用 lark-oapi SDK 和 WebSocket 的飞书/Lark 通道。"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import TYPE_CHECKING, Any

from loguru import logger
from ultrabot.channels.base import BaseChannel

if TYPE_CHECKING:
    from ultrabot.bus.events import OutboundMessage
    from ultrabot.bus.queue import MessageBus


class FeishuChannel(BaseChannel):
    """飞书通道 — WebSocket，无需公网 IP。"""

    @property
    def name(self) -> str:
        return "feishu"

    def __init__(self, config: dict, bus: "MessageBus") -> None:
        super().__init__(config, bus)
        self._app_id = config.get("appId", "")
        self._app_secret = config.get("appSecret", "")
        self._encrypt_key = config.get("encryptKey", "")
        self._react_emoji = config.get("reactEmoji", "THUMBSUP")
        self._group_policy = config.get("groupPolicy", "mention")
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ws_thread: threading.Thread | None = None
        self._client: Any = None
        self._ws_client: Any = None

    async def start(self) -> None:
        import lark_oapi as lark

        self._loop = asyncio.get_running_loop()
        self._running = True

        # 用于发送消息的 Lark 客户端。
        self._client = (lark.Client.builder()
            .app_id(self._app_id)
            .app_secret(self._app_secret)
            .build())

        # 事件分发器。
        event_handler = (lark.EventDispatcherHandler.builder(
                self._encrypt_key, "")
            .register_p2_im_message_receive_v1(self._on_message_sync)
            .build())

        self._ws_client = lark.ws.Client(
            self._app_id, self._app_secret,
            event_handler=event_handler,
        )

        # 在专用线程中运行 WebSocket — 避免事件循环冲突。
        def _run_ws():
            import lark_oapi.ws.client as _lark_ws_client
            ws_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(ws_loop)
            _lark_ws_client.loop = ws_loop
            try:
                while self._running:
                    try:
                        self._ws_client.start()
                    except Exception:
                        if self._running:
                            time.sleep(5)
            finally:
                ws_loop.close()

        self._ws_thread = threading.Thread(target=_run_ws, daemon=True)
        self._ws_thread.start()

    def _on_message_sync(self, data: Any) -> None:
        """WS 线程中的同步回调 → 在主循环上调度异步工作。"""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._on_message(data), self._loop
            )

    async def _on_message(self, data: Any) -> None:
        """处理飞书消息事件并发布到消息总线。"""
        from ultrabot.bus.events import InboundMessage

        try:
            event = data.event if hasattr(data, "event") else data
            message = getattr(event, "message", None)
            if message is None:
                return

            msg_type = getattr(message, "message_type", "")
            chat_id = getattr(message, "chat_id", "")
            msg_id = getattr(message, "message_id", "")
            sender = getattr(event, "sender", None)
            sender_id = ""
            if sender:
                sender_id_obj = getattr(sender, "sender_id", None)
                if sender_id_obj:
                    sender_id = getattr(sender_id_obj, "open_id", "") or ""

            # 群组消息策略：只处理 @提及
            chat_type = getattr(message, "chat_type", "")
            if chat_type == "group" and self._group_policy == "mention":
                mentions = getattr(message, "mentions", None)
                if not mentions:
                    return

            # 解析消息内容
            content = ""
            raw_content = getattr(message, "content", "")
            if raw_content and msg_type == "text":
                try:
                    content_data = json.loads(raw_content)
                    content = content_data.get("text", "")
                except (json.JSONDecodeError, TypeError):
                    content = raw_content

            if not content:
                return

            logger.info("Feishu message from {} in {}: {}",
                        sender_id, chat_id, content[:50])
            await self.bus.publish(InboundMessage(
                channel=self.name,
                sender_id=sender_id,
                chat_id=chat_id,
                content=content,
                metadata={"msg_id": msg_id},
            ))
        except Exception as exc:
            logger.error("Feishu _on_message error: {}", exc)

    async def send(self, msg: "OutboundMessage") -> None:
        """通过 Lark API 发送消息。"""
        import lark_oapi as lark
        from lark_oapi.api.im.v1 import (
            CreateMessageRequest,
            CreateMessageRequestBody,
        )

        try:
            body = (CreateMessageRequestBody.builder()
                .receive_id(msg.chat_id)
                .msg_type("text")
                .content(json.dumps({"text": msg.content}))
                .build())
            req = (CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(body)
                .build())
            resp = self._client.im.v1.message.create(req)
            if not resp.success():
                logger.error("Feishu send failed: {} {}",
                             resp.code, resp.msg)
        except Exception as exc:
            logger.error("Feishu send error: {}", exc)
            raise

    async def stop(self) -> None:
        """优雅关闭飞书 WebSocket 连接和线程。"""
        self._running = False
        # WebSocket 线程是 daemon 线程，会自动退出
        if self._ws_thread is not None:
            self._ws_thread.join(timeout=5)
            self._ws_thread = None
        self._client = None
        self._ws_client = None
        logger.info("FeishuChannel stopped")
