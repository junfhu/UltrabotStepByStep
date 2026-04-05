# ultrabot/gateway/server.py
"""网关服务器 — 将通道、智能体和消息总线连接在一起。"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import signal
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from ultrabot.bus.events import InboundMessage
    from ultrabot.config.schema import Config


# 通道名称 -> (模块路径, 类名)
_CHANNEL_MAP: dict[str, tuple[str, str]] = {
    "telegram": ("ultrabot.channels.telegram", "TelegramChannel"),
}


class Gateway:
    """主网关，启动所有运行时组件并处理消息。

    生命周期：
        1. start() 初始化消息总线、智能体、通道。
        2. MessageBus 分发循环读取入站消息，传递给
           智能体，并将响应通过通道发送回去。
        3. stop() 优雅地关闭所有组件。
    """

    def __init__(self, config: "Config", channels_cfg: dict | None = None) -> None:
        self._config = config
        self._channels_cfg = channels_cfg or {}
        self._running = False

    async def start(self) -> None:
        """初始化所有组件并进入主事件循环。"""
        logger.info("Gateway starting up")

        from openai import OpenAI
        from ultrabot.agent import Agent
        from ultrabot.bus.queue import MessageBus
        from ultrabot.channels.base import ChannelManager
        from ultrabot.session.manager import SessionManager
        from ultrabot.tools.base import ToolRegistry
        from ultrabot.tools.builtin import register_builtin_tools
        from ultrabot.tools.toolsets import ToolsetManager, register_default_toolsets

        # ── 消息总线 ──
        self._bus = MessageBus()

        # ── 工具注册 ──
        registry = ToolRegistry()
        register_builtin_tools(registry)
        manager = ToolsetManager(registry)
        register_default_toolsets(manager)
        active_tools = manager.resolve(["all"])
        filtered_registry = ToolRegistry()
        for tool in active_tools:
            filtered_registry.register(tool)

        # ── 会话管理 ──
        data_dir = Path.home() / ".ultrabot"
        self._session_mgr = SessionManager(data_dir)

        # ── LLM 客户端和智能体 ──
        defaults = self._config.agents.defaults
        provider_name = defaults.provider
        prov_cfg = self._config.providers.all_providers().get(provider_name)

        api_key = (prov_cfg.api_key if prov_cfg else None) or os.getenv("OPENAI_API_KEY")
        api_base = (prov_cfg.api_base if prov_cfg else None) or os.getenv("OPENAI_BASE_URL")

        client = OpenAI(api_key=api_key, base_url=api_base)
        model = defaults.model

        self._agent = Agent(
            client=client,
            model=model,
            tool_registry=filtered_registry,
            sessions=self._session_mgr,
            context_window=defaults.context_window_tokens,
        )

        # ── 注册入站处理器 ──
        self._bus.set_inbound_handler(self._handle_inbound)

        # ── 通道管理 ──
        self._channel_mgr = ChannelManager(self._channels_cfg, self._bus)
        self._register_channels()
        await self._channel_mgr.start_all()

        # ── 信号处理（优雅关闭）──
        try:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(
                    sig, lambda: asyncio.create_task(self.stop())
                )
        except NotImplementedError:
            pass  # Windows 不支持 add_signal_handler

        self._running = True
        logger.info("Gateway started — dispatching messages")

        try:
            await self._bus.dispatch_inbound()
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    async def _handle_inbound(self, inbound: "InboundMessage"):
        """处理单条入站消息 -> 智能体 -> 出站。"""
        from ultrabot.bus.events import OutboundMessage

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

    def _register_channels(self) -> None:
        """根据配置实例化和注册已启用的通道。"""
        for name, (module_path, class_name) in _CHANNEL_MAP.items():
            cfg = self._channels_cfg.get(name)
            if not cfg:
                continue
            if not cfg.get("enabled", False):
                logger.info("Channel '{}' disabled — skipping", name)
                continue
            try:
                mod = importlib.import_module(module_path)
                cls = getattr(mod, class_name)
                self._channel_mgr.register(cls(cfg, self._bus))
                logger.info("Channel '{}' registered", name)
            except ImportError as exc:
                logger.warning("Channel '{}' deps not installed — skipping: {}", name, exc)
            except Exception:
                logger.exception("Failed to create channel '{}'", name)

    async def stop(self) -> None:
        """优雅地关闭所有组件。"""
        if not self._running:
            return
        self._running = False
        logger.info("Gateway shutting down")

        self._bus.shutdown()
        await self._channel_mgr.stop_all()

        logger.info("Gateway stopped")
