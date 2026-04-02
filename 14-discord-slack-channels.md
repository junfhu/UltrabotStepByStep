# 课程 14：Discord + Slack 通道

**目标：** 添加 Discord 和 Slack 作为消息通道，演示新平台如何接入相同的 BaseChannel 接口。

**你将学到：**
- Discord.py：intents、`on_message` 事件、2000 字符分块
- Slack-sdk：Socket Mode、即时 `ack()` 模式
- 平台特定的格式差异
- 相同的 `BaseChannel` 契约如何使每个通道可互换

**新建文件：**
- `ultrabot/channels/discord_channel.py` — `DiscordChannel`
- `ultrabot/channels/slack_channel.py` — `SlackChannel`

### 步骤 1：DiscordChannel

Discord 使用 `discord.py` 通过 WebSocket 连接。我们必须声明
`message_content` intent 才能读取消息文本。

创建 `ultrabot/channels/discord_channel.py`：

```python
"""使用 discord.py 的 Discord 通道。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from loguru import logger
from ultrabot.channels.base import BaseChannel

if TYPE_CHECKING:
    from ultrabot.bus.events import OutboundMessage
    from ultrabot.bus.queue import MessageBus

try:
    import discord
    _DISCORD_AVAILABLE = True
except ImportError:
    _DISCORD_AVAILABLE = False


def _require_discord() -> None:
    if not _DISCORD_AVAILABLE:
        raise ImportError(
            "discord.py is required. Install: pip install 'ultrabot-ai[discord]'"
        )


class DiscordChannel(BaseChannel):
    """Discord 通道适配器。"""

    @property
    def name(self) -> str:
        return "discord"

    def __init__(self, config: dict, bus: "MessageBus") -> None:
        _require_discord()
        super().__init__(config, bus)
        self._token: str = config["token"]
        self._allow_from: list[int] | None = config.get("allowFrom")
        self._allowed_guilds: list[int] | None = config.get("allowedGuilds")
        self._client: Any = None
        self._run_task: asyncio.Task | None = None
```

### 步骤 2：Discord 访问控制和事件

```python
    def _is_allowed(self, user_id: int, guild_id: int | None) -> bool:
        if self._allow_from and user_id not in self._allow_from:
            return False
        if self._allowed_guilds and guild_id and guild_id not in self._allowed_guilds:
            return False
        return True

    async def start(self) -> None:
        _require_discord()

        # message_content intent 是读取消息文本所必需的。
        intents = discord.Intents.default()
        intents.message_content = True
        self._client = discord.Client(intents=intents)
        channel_ref = self   # 为闭包捕获引用

        @self._client.event
        async def on_ready():
            logger.info("Discord bot connected as {}", self._client.user)

        @self._client.event
        async def on_message(message: discord.Message):
            if message.author == self._client.user:
                return   # 忽略我们自己的消息

            user_id = message.author.id
            guild_id = message.guild.id if message.guild else None
            if not channel_ref._is_allowed(user_id, guild_id):
                return

            from ultrabot.bus.events import InboundMessage
            inbound = InboundMessage(
                channel="discord",
                sender_id=str(user_id),
                chat_id=str(message.channel.id),
                content=message.content,
                metadata={
                    "user_name": str(message.author),
                    "guild_id": str(guild_id) if guild_id else None,
                },
            )
            await channel_ref.bus.publish(inbound)

        self._running = True
        self._run_task = asyncio.create_task(self._client.start(self._token))
```

### 步骤 3：Discord 出站 — 2000 字符分块

```python
    async def stop(self) -> None:
        self._running = False
        if self._client:
            await self._client.close()
        if self._run_task:
            self._run_task.cancel()

    async def send(self, message: "OutboundMessage") -> None:
        if self._client is None:
            raise RuntimeError("DiscordChannel not started")

        channel = self._client.get_channel(int(message.chat_id))
        if channel is None:
            channel = await self._client.fetch_channel(int(message.chat_id))

        text = message.content
        # Discord 限制为 2000 字符 — 必要时进行分块。
        max_len = 2000
        for i in range(0, len(text), max_len):
            await channel.send(text[i : i + max_len])

    async def send_typing(self, chat_id: str | int) -> None:
        if self._client is None:
            return
        channel = self._client.get_channel(int(chat_id))
        if channel:
            await channel.typing()
```

### 步骤 4：SlackChannel — Socket Mode

Slack 使用 Socket Mode（WebSocket）而不是 HTTP webhook，因此不需要
公网 URL。关键模式是**即时确认** — 你必须在 3 秒内调用 `ack()`，否则
Slack 会重试该事件。

创建 `ultrabot/channels/slack_channel.py`：

```python
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
```

### 步骤 5：Slack 生命周期和即时确认

```python
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
        # 注册我们的事件监听器。
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
        # 立即确认 — 如果 3 秒内不确认，Slack 会重试。
        response = SocketModeResponse(envelope_id=req.envelope_id)
        await client.send_socket_mode_response(response)

        if req.type != "events_api":
            return

        event = req.payload.get("event", {})
        if event.get("type") != "message" or event.get("subtype"):
            return   # 忽略机器人消息、编辑等

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
```

### 平台对比

| 特性 | Telegram | Discord | Slack |
|------|----------|---------|-------|
| 连接方式 | HTTP 轮询 | WebSocket | Socket Mode (WS) |
| 最大消息长度 | 4096 字符 | 2000 字符 | ~40k 字符 |
| 输入指示器 | 有 | 有 | 无 |
| 认证方式 | Bot token | Bot token + intents | Bot token + App token |
| 需要快速确认？ | 否 | 否 | **是（3秒）** |

### 测试

```python
# tests/test_channels_platform.py
"""验证通道类可以加载并具有正确的接口。"""


def test_discord_channel_has_correct_name():
    # 导入时不需要在运行时依赖 discord 库。
    from ultrabot.channels.discord_channel import DiscordChannel
    assert DiscordChannel.name.fget is not None   # 属性存在


def test_slack_channel_has_correct_name():
    from ultrabot.channels.slack_channel import SlackChannel
    assert SlackChannel.name.fget is not None


def test_base_channel_is_abstract():
    from ultrabot.channels.base import BaseChannel
    import inspect
    abstract_methods = {
        name for name, _ in inspect.getmembers(BaseChannel)
        if getattr(getattr(BaseChannel, name, None), "__isabstractmethod__", False)
    }
    assert "start" in abstract_methods
    assert "stop" in abstract_methods
    assert "send" in abstract_methods
    assert "name" in abstract_methods
```

### 检查点

```bash
python -m pytest tests/test_channels_platform.py -v
```

预期结果：全部 3 个测试通过。要进行实际测试，将机器人令牌添加到配置中，
启用通道，然后运行网关。

### 本课成果

两个新的通道实现 — `DiscordChannel`（WebSocket intents、2000 字符
分块）和 `SlackChannel`（Socket Mode、即时确认）— 都接入
相同的 `BaseChannel` 接口，无需对智能体或消息总线做任何改动。

---
