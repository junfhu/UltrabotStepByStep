# ultrabot/channels/slack_channel.py
"""使用 slack-sdk 和 Socket Mode 的 Slack 通道。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from loguru import logger
from ultrabot.channels.base import BaseChannel

if TYPE_CHECKING:
    from ultrabot.bus.events import OutboundMessage
    from ultrabot.bus.queue import MessageBus

try:
    from slack_sdk.web.async_client import AsyncWebClient
    from slack_sdk.socket_mode.aiohttp import SocketModeClient
    from slack_sdk.socket_mode.request import SocketModeRequest
    from slack_sdk.socket_mode.response import SocketModeResponse
    _SLACK_AVAILABLE = True
except ImportError:
    _SLACK_AVAILABLE = False


def _require_slack() -> None:
    if not _SLACK_AVAILABLE:
        raise ImportError(
            "slack-sdk is required. Install: pip install 'ultrabot-ai[slack]'"
        )


class SlackChannel(BaseChannel):
    """使用 Socket Mode 的 Slack 通道适配器。"""

    @property
    def name(self) -> str:
        return "slack"

    def __init__(self, config: dict, bus: "MessageBus") -> None:
        _require_slack()
        super().__init__(config, bus)
        self._bot_token: str = config["botToken"]
        self._app_token: str = config["appToken"]
        self._allow_from: list[str] | None = config.get("allowFrom")
        self._web_client: Any = None
        self._socket_client: Any = None

    def _is_allowed(self, user_id: str) -> bool:
        if not self._allow_from:
            return True
        return user_id in self._allow_from

    async def start(self) -> None:
        _require_slack()
        self._web_client = AsyncWebClient(token=self._bot_token)
        self._socket_client = SocketModeClient(
            app_token=self._app_token,
            web_client=self._web_client,
        )
        self._socket_client.socket_mode_request_listeners.append(
            self._handle_event
        )
        await self._socket_client.connect()
        self._running = True
        logger.info("Slack channel started (Socket Mode)")

    async def stop(self) -> None:
        self._running = False
        if self._socket_client:
            await self._socket_client.close()

    async def _handle_event(self, client: Any, req: "SocketModeRequest") -> None:
        response = SocketModeResponse(envelope_id=req.envelope_id)
        await client.send_socket_mode_response(response)

        if req.type != "events_api":
            return

        event = req.payload.get("event", {})
        if event.get("type") != "message" or event.get("subtype"):
            return

        user_id = event.get("user", "")
        if not self._is_allowed(user_id):
            return

        from ultrabot.bus.events import InboundMessage
        inbound = InboundMessage(
            channel="slack",
            sender_id=user_id,
            chat_id=event.get("channel", ""),
            content=event.get("text", ""),
        )
        await self.bus.publish(inbound)

    async def send(self, message: "OutboundMessage") -> None:
        if self._web_client is None:
            raise RuntimeError("SlackChannel not started")
        await self._web_client.chat_postMessage(
            channel=message.chat_id,
            text=message.content,
        )

    async def send_typing(self, chat_id: str | int) -> None:
        """Slack 没有持久的输入指示器 — 无操作。"""
