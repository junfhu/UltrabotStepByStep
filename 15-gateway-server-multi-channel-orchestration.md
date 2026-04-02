# 课程 15：网关服务器 — 多通道编排

**目标：** 构建网关，将智能体、消息总线、会话管理器、安全守卫和所有通道连接成一个可运行的服务器。

**你将学到：**
- 将所有组件组合在一个 `Gateway` 类之后
- 配置驱动的通道注册
- 入站处理器管道：通道 → 消息总线 → 智能体 → 通道
- 优雅关闭的信号处理（`SIGINT`、`SIGTERM`）
- 从用户输入到机器人响应的完整消息流程

**新建文件：**
- `ultrabot/gateway/__init__.py` — 公共重导出
- `ultrabot/gateway/server.py` — `Gateway` 类

### 步骤 1：Gateway 骨架

创建 `ultrabot/gateway/server.py`：

```python
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
```

### 步骤 2：启动所有组件

```python
    async def start(self) -> None:
        """初始化所有组件并进入主事件循环。"""
        logger.info("Gateway starting up")

        # 延迟导入以避免循环依赖。
        from ultrabot.bus.queue import MessageBus
        from ultrabot.providers.manager import ProviderManager
        from ultrabot.session.manager import SessionManager
        from ultrabot.tools.base import ToolRegistry
        from ultrabot.agent.agent import Agent
        from ultrabot.channels.base import ChannelManager

        # 从配置派生工作空间路径。
        workspace = Path(
            self._config.agents.defaults.workspace
        ).expanduser().resolve()
        workspace.mkdir(parents=True, exist_ok=True)

        # 核心组件。
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

        # 在消息总线上注册入站处理器。
        self._bus.set_inbound_handler(self._handle_inbound)

        # 通道 — 配置驱动的注册。
        channels_cfg = self._config.channels
        extra_dict: dict = channels_cfg.model_extra or {}
        self._channel_mgr = ChannelManager(extra_dict, self._bus)
        self._register_channels(extra_dict)
        await self._channel_mgr.start_all()

        # 用于优雅关闭的信号处理器。
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(
                sig, lambda: asyncio.create_task(self.stop())
            )

        self._running = True
        logger.info("Gateway started — dispatching messages")

        try:
            await self._bus.dispatch_inbound()  # 阻塞直到关闭
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()
```

### 步骤 3：入站处理器

这是核心管道：从消息总线接收入站消息，发送输入指示器，
运行智能体，然后通过发起通道将响应发送回去。

```python
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

        # 在智能体思考时显示"正在输入..."。
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
```

### 步骤 4：配置驱动的通道注册

```python
    def _register_channels(self, channels_extra: dict) -> None:
        """根据配置实例化和注册已启用的通道。"""

        def _is_enabled(cfg) -> bool:
            if isinstance(cfg, dict):
                return cfg.get("enabled", False)
            return getattr(cfg, "enabled", False)

        def _to_dict(cfg) -> dict:
            return cfg if isinstance(cfg, dict) else cfg.__dict__

        # 每个通道条件导入并注册。
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
```

### 步骤 5：优雅关闭

```python
    async def stop(self) -> None:
        """优雅地关闭所有组件。"""
        if not self._running:
            return
        self._running = False
        logger.info("Gateway shutting down")

        self._bus.shutdown()
        await self._channel_mgr.stop_all()

        logger.info("Gateway stopped")
```

### 消息流程图

```
 用户在 Telegram 中输入
       │
       ▼
 TelegramChannel._handle_message()
       │  创建 InboundMessage
       ▼
 MessageBus.publish()     ← 优先级队列
       │
       ▼
 MessageBus.dispatch_inbound()
       │  从队列中拉取
       ▼
 Gateway._handle_inbound()
       │  发送输入指示器
       │  调用 Agent.run()
       │     │  SessionManager.get_or_create()
       │     │  ProviderManager.chat_with_failover()
       │     │  ToolRegistry.execute()（如需要）
       │     │  Session.trim()
       │     ▼
       │  返回响应文本
       ▼
 OutboundMessage
       │
       ▼
 TelegramChannel.send_with_retry()
       │  按 4096 字符分块
       ▼
 用户看到响应
```

### 包初始化

```python
# ultrabot/gateway/__init__.py
"""网关包 — 编排通道、智能体和消息总线。"""

from ultrabot.gateway.server import Gateway

__all__ = ["Gateway"]
```

### 测试

```python
# tests/test_gateway.py
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from ultrabot.bus.events import InboundMessage, OutboundMessage
from ultrabot.bus.queue import MessageBus
from ultrabot.channels.base import ChannelManager


def test_inbound_handler_calls_agent_and_sends_response():
    """在不启动真实通道的情况下模拟网关的入站处理器。"""
    async def _run():
        bus = MessageBus()

        # 模拟智能体
        mock_agent = AsyncMock()
        mock_agent.run.return_value = "Hello from the agent!"

        # 模拟通道
        mock_channel = AsyncMock()
        mock_channel.name = "test"

        # 模拟通道管理器
        mock_mgr = MagicMock(spec=ChannelManager)
        mock_mgr.get_channel.return_value = mock_channel

        # 模拟处理器逻辑
        inbound = InboundMessage(
            channel="test", sender_id="u1",
            chat_id="c1", content="Hi bot"
        )

        channel = mock_mgr.get_channel(inbound.channel)
        await channel.send_typing(inbound.chat_id)

        response_text = await mock_agent.run(
            inbound.content, session_key=inbound.session_key,
        )
        outbound = OutboundMessage(
            channel=inbound.channel,
            chat_id=inbound.chat_id,
            content=response_text,
        )
        await channel.send_with_retry(outbound)

        # 验证
        mock_agent.run.assert_called_once()
        channel.send_with_retry.assert_called_once()
        assert outbound.content == "Hello from the agent!"

    asyncio.run(_run())


def test_gateway_module_exports():
    from ultrabot.gateway import Gateway
    assert Gateway is not None
```

### 检查点

```bash
python -m pytest tests/test_gateway.py -v
```

预期结果：两个测试全部通过。要运行完整的网关：

```bash
python -m ultrabot gateway
```

这将启动消息总线分发循环，注册所有已启用的通道，并开始
处理消息。在任何已配置的平台上发送消息，即可观察
智能体的响应。

### 本课成果

一个 `Gateway` 类，组合了智能体、消息总线、会话管理器、提供者
管理器和所有通道适配器。配置驱动的通道注册意味着
启用一个新平台只需一行配置更改。信号处理器确保
在 `Ctrl+C` 时干净地关闭。

---
