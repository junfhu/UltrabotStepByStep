# ultrabot/gateway/server.py
"""网关服务器 — 将通道、智能体和消息总线连接在一起。"""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from ultrabot.config.schema import Config


class Gateway:
    """主网关，启动所有运行时组件并处理消息。

    生命周期：
        1. start() 初始化消息总线、提供者、会话、智能体、通道。
        2. MessageBus 分发循环读取入站消息，传递给
           智能体，并将响应通过通道发送回去。
        3. stop() 优雅地关闭所有组件。
    """

    def __init__(self, config: "Config") -> None:
        self._config = config
        self._running = False
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        """初始化所有组件并进入主事件循环。"""
        logger.info("Gateway starting up")

        from ultrabot.bus.queue import MessageBus
        from ultrabot.providers.manager import ProviderManager
        from ultrabot.session.manager import SessionManager
        from ultrabot.tools.base import ToolRegistry
        from ultrabot.agent.agent import Agent
        from ultrabot.channels.base import ChannelManager

        workspace = Path(
            self._config.agents.defaults.workspace
        ).expanduser().resolve()
        workspace.mkdir(parents=True, exist_ok=True)

        self._bus = MessageBus()
        self._provider_mgr = ProviderManager(self._config)
        self._session_mgr = SessionManager(workspace)
        self._tool_registry = ToolRegistry()
        self._agent = Agent(
            config=self._config.agents.defaults,
            provider_manager=self._provider_mgr,
            session_manager=self._session_mgr,
            tool_registry=self._tool_registry,
        )

        self._bus.set_inbound_handler(self._handle_inbound)

        channels_cfg = self._config.channels
        extra_dict: dict = channels_cfg.model_extra or {}
        self._channel_mgr = ChannelManager(extra_dict, self._bus)
        self._register_channels(extra_dict)
        await self._channel_mgr.start_all()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(
                sig, lambda: asyncio.create_task(self.stop())
            )

        self._running = True
        logger.info("Gateway started — dispatching messages")

        try:
            await self._bus.dispatch_inbound()
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    async def _handle_inbound(self, inbound):
        """处理单条入站消息 -> 智能体 -> 出站。"""
        from ultrabot.bus.events import InboundMessage, OutboundMessage

        assert isinstance(inbound, InboundMessage)
        logger.info("Processing message from {} on {}",
                     inbound.sender_id, inbound.channel)

        channel = self._channel_mgr.get_channel(inbound.channel)
        if channel is None:
            logger.error("No channel for '{}'", inbound.channel)
            return None

        await channel.send_typing(inbound.chat_id)

        try:
            response_text = await self._agent.run(
                inbound.content,
                session_key=inbound.session_key,
            )
            outbound = OutboundMessage(
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                content=response_text,
            )
            await channel.send_with_retry(outbound)
            return outbound
        except Exception:
            logger.exception("Error processing message")
            return None

    def _register_channels(self, channels_extra: dict) -> None:
        """根据配置实例化和注册已启用的通道。"""

        def _is_enabled(cfg) -> bool:
            if isinstance(cfg, dict):
                return cfg.get("enabled", False)
            return getattr(cfg, "enabled", False)

        def _to_dict(cfg) -> dict:
            return cfg if isinstance(cfg, dict) else cfg.__dict__

        channel_map = {
            "telegram":  ("ultrabot.channels.telegram", "TelegramChannel"),
            "discord":   ("ultrabot.channels.discord_channel", "DiscordChannel"),
            "slack":     ("ultrabot.channels.slack_channel", "SlackChannel"),
            "feishu":    ("ultrabot.channels.feishu", "FeishuChannel"),
            "qq":        ("ultrabot.channels.qq", "QQChannel"),
            "wecom":     ("ultrabot.channels.wecom", "WecomChannel"),
            "weixin":    ("ultrabot.channels.weixin", "WeixinChannel"),
        }

        for name, (module_path, class_name) in channel_map.items():
            cfg = channels_extra.get(name)
            if not cfg or not _is_enabled(cfg):
                continue
            try:
                import importlib
                mod = importlib.import_module(module_path)
                cls = getattr(mod, class_name)
                self._channel_mgr.register(cls(_to_dict(cfg), self._bus))
            except ImportError:
                logger.warning("{} deps not installed — skipping", name)

    async def stop(self) -> None:
        """优雅地关闭所有组件。"""
        if not self._running:
            return
        self._running = False
        logger.info("Gateway shutting down")

        self._bus.shutdown()
        await self._channel_mgr.stop_all()

        logger.info("Gateway stopped")
