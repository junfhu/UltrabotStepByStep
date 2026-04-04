# Ultrabot：30 课程开发指南
**从零开始构建一个生产级 AI 助手框架。**
本指南将带你从"向 LLM 问好"一步步走到一个完整的多提供者、多通道 AI 智能体，具备工具调用、记忆、安全防护和 Web 界面。每节课程都建立在上一节课的基础之上。每节课都包含可运行的代码和测试。  
本教程的主要思路来自于
- Nanobot (https://github.com/HKUDS/nanobot)
- Learn-Claude-Code (https://github.com/shareAI-lab/learn-claude-code/)

本课程设计由AI辅助下完成，因为课程自身也在不停修正，请参考 https://github.com/junfhu/UltrabotStepByStep，如果您觉得对您有帮助，请帮助点亮一颗星。  
本课程中使用的大模型提供商是火山引擎Code Plan，如果正好你也需要，可以使用我的邀请码获取9折优惠 https://volcengine.com/L/_01BJCkKdMc/  邀请码：HHCDB4J4）  



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

## 本课使用的 Python 知识

### `try / except ImportError`（可选依赖检测）

通过 `try/except ImportError` 来检测第三方库是否安装，用一个布尔标志记录结果。这样模块本身总能被导入，只有在真正需要时才检查依赖。

```python
try:
    import discord
    _DISCORD_AVAILABLE = True
except ImportError:
    _DISCORD_AVAILABLE = False

def _require_discord():
    if not _DISCORD_AVAILABLE:
        raise ImportError("请安装 discord.py: pip install discord.py")
```

**为什么在本课中使用：** Discord 和 Slack 都是可选依赖。用户可能只需要其中一个平台。通过这种模式，没安装 `discord.py` 的用户仍然可以使用 Slack 通道，反之亦然。

### 类继承（Inheritance）

Python 通过 `class Child(Parent)` 实现继承。子类继承父类的所有方法和属性，同时可以重写（override）其中一些。

```python
class BaseChannel:
    async def send_with_retry(self, message, max_retries=3):
        # 通用的重试逻辑
        ...

class DiscordChannel(BaseChannel):
    async def send(self, message):
        # Discord 特有的发送逻辑
        ...
    # send_with_retry() 从父类继承，无需重写
```

**为什么在本课中使用：** `DiscordChannel` 和 `SlackChannel` 都继承自 `BaseChannel`，复用了父类的 `send_with_retry()`、`send_typing()` 等方法，只需实现各自平台特有的 `name`、`start()`、`stop()`、`send()`。

### `asyncio.Task` 和 `asyncio.create_task()`（异步任务）

`asyncio.create_task()` 把一个协程包装成 Task 对象，让它在事件循环中后台运行。Task 可以被取消（`cancel()`）、等待（`await`）或检查状态。

```python
import asyncio

async def long_running():
    while True:
        await asyncio.sleep(1)
        print("Running...")

task = asyncio.create_task(long_running())  # 后台启动
# ... 做其他事情 ...
task.cancel()  # 需要时取消
```

**为什么在本课中使用：** Discord 客户端的 `start()` 方法是一个永远运行的协程（保持 WebSocket 连接）。用 `create_task()` 把它放到后台运行，这样 `DiscordChannel.start()` 方法可以立即返回，不阻塞其他通道的启动。

### 闭包（Closure）

闭包是指一个内部函数可以"记住"并访问外部函数的变量，即使外部函数已经执行完毕。

```python
def make_greeter(prefix):
    def greet(name):
        return f"{prefix}, {name}!"  # 引用了外部变量 prefix
    return greet

hello = make_greeter("Hello")
print(hello("Alice"))  # "Hello, Alice!"
```

**为什么在本课中使用：** Discord 事件处理器 `on_message` 定义在 `start()` 方法内部，需要访问 `self` 引用。通过 `channel_ref = self` 创建闭包捕获，内部的 `on_message` 函数就能通过 `channel_ref` 访问通道实例的方法和属性。

### `@self._client.event` 装饰器（事件注册）

Discord.py 使用装饰器模式注册事件处理器。`@client.event` 将一个 async 函数注册为特定事件的回调。

```python
@self._client.event
async def on_ready():
    print(f"Bot connected as {self._client.user}")

@self._client.event
async def on_message(message):
    print(f"Received: {message.content}")
```

**为什么在本课中使用：** Discord.py 框架要求用 `@client.event` 装饰器注册 `on_ready` 和 `on_message` 等事件回调。这是 Discord.py 的标准用法，让框架知道"当有消息到来时，调用这个函数"。

### `discord.Intents`（Discord 权限声明）

Discord 的 Intents 系统控制机器人能接收哪些事件。必须声明需要的权限（intents），否则相关事件将不会被推送。

```python
intents = discord.Intents.default()        # 基础权限
intents.message_content = True             # 额外请求：读取消息内容
client = discord.Client(intents=intents)
```

**为什么在本课中使用：** Discord 从 2022 年起要求机器人显式声明 `message_content` intent 才能读取消息文本。不声明的话，`message.content` 会是空字符串，机器人就无法处理用户消息。

### 字符串切片分块（Message Chunking）

利用字符串切片和 `range()` 把长文本按平台限制分成多个块发送。

```python
text = "A very long message..."
max_len = 2000  # Discord 限制
for i in range(0, len(text), max_len):
    await channel.send(text[i : i + max_len])
```

**为什么在本课中使用：** Discord 单条消息限制 2000 字符（Telegram 是 4096）。`send()` 方法用循环 + 切片把超长消息分成多块逐一发送，确保不超过平台限制。

### Socket Mode 模式（Slack WebSocket）

Slack 的 Socket Mode 通过 WebSocket 连接接收事件，不需要公网 HTTP 端点。关键要求是收到事件后 3 秒内必须调用 `ack()` 确认。

```python
from slack_sdk.socket_mode.response import SocketModeResponse

async def _handle_event(self, client, req):
    # 立即确认 — 超过 3 秒 Slack 会重试
    response = SocketModeResponse(envelope_id=req.envelope_id)
    await client.send_socket_mode_response(response)

    # 然后慢慢处理事件...
    event = req.payload.get("event", {})
```

**为什么在本课中使用：** Slack 的 Socket Mode 让开发者不需要部署公网服务器就能接收事件。即时确认模式（先 ack 再处理）是 Slack 的硬性要求，不遵守会导致事件被重复推送。

### `dict.get(key, default)`（安全字典访问）

`dict.get()` 在键不存在时返回默认值，不会抛出异常。

```python
event = {"type": "message", "text": "hello"}
user = event.get("user", "")       # 键存在就返回值
subtype = event.get("subtype")     # 键不存在返回 None
```

**为什么在本课中使用：** Slack 事件的 payload 结构不固定，某些字段可能缺失。使用 `get()` 安全地获取 `event`、`type`、`user`、`channel`、`text` 等字段，避免 `KeyError` 导致程序崩溃。

### `inspect` 模块（反射/内省）

`inspect` 模块提供了查看 Python 对象内部结构的工具，如获取类的成员、检查方法是否为抽象方法等。

```python
import inspect

abstract_methods = {
    name for name, _ in inspect.getmembers(BaseChannel)
    if getattr(getattr(BaseChannel, name, None), "__isabstractmethod__", False)
}
print(abstract_methods)  # {'start', 'stop', 'send', 'name'}
```

**为什么在本课中使用：** 测试代码用 `inspect.getmembers()` 和 `__isabstractmethod__` 属性来验证 `BaseChannel` 确实定义了 `start`、`stop`、`send`、`name` 四个抽象方法，确保接口契约完整。

### 集合推导式（Set Comprehension）

集合推导式用 `{表达式 for 变量 in 可迭代对象 if 条件}` 创建集合，语法与列表推导式类似但用花括号。集合自动去重。

```python
numbers = [1, 2, 2, 3, 3, 3]
unique_squares = {x * x for x in numbers}  # {1, 4, 9}
```

**为什么在本课中使用：** 测试代码用集合推导式 `{name for name, _ in inspect.getmembers(BaseChannel) if ...}` 收集所有抽象方法名，然后用 `in` 运算符检查特定方法是否存在。集合的查找效率是 O(1)。

### `getattr()` 动态属性访问

`getattr(obj, name, default)` 可以用字符串来访问对象的属性。如果属性不存在，返回默认值。

```python
class Config:
    enabled = True

config = Config()
value = getattr(config, "enabled", False)   # True
missing = getattr(config, "debug", False)   # False（属性不存在）
```

**为什么在本课中使用：** 测试中 `getattr(getattr(BaseChannel, name, None), "__isabstractmethod__", False)` 用两层 `getattr` 检查一个属性是否标记为抽象方法。这种动态访问方式适用于在运行时根据名称查找属性。

### `task.cancel()`（任务取消）

调用 `task.cancel()` 会向运行中的异步任务发送一个 `CancelledError`，使其停止执行。

```python
task = asyncio.create_task(some_long_running_coroutine())
# ... 之后需要停止 ...
task.cancel()  # 发送取消信号
```

**为什么在本课中使用：** `DiscordChannel.stop()` 中调用 `self._run_task.cancel()` 来取消后台运行的 Discord 客户端任务，实现优雅关闭。

### `TYPE_CHECKING` 条件导入

`typing.TYPE_CHECKING` 只在类型检查工具运行时为 `True`，运行时为 `False`。用于导入仅用于类型注解的模块，避免运行时循环依赖。

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ultrabot.bus.events import OutboundMessage

class MyChannel:
    async def send(self, message: "OutboundMessage") -> None:
        ...
```

**为什么在本课中使用：** Discord 和 Slack 通道文件都用 `TYPE_CHECKING` 导入 `OutboundMessage` 和 `MessageBus`，这些类型只在注解中使用，运行时不需要加载。

### `from __future__ import annotations`（延迟注解评估）

让类型注解在运行时保存为字符串而不立即求值，支持前向引用和新语法。

```python
from __future__ import annotations

class DiscordChannel(BaseChannel):
    def __init__(self, config: dict, bus: "MessageBus") -> None:
        ...
```

**为什么在本课中使用：** 配合 `TYPE_CHECKING` 使用，确保用字符串形式引用的类型（如 `"MessageBus"`、`"OutboundMessage"`）在运行时不会因为未导入而报错。

### `str()` 类型转换

`str()` 将其他类型的值转换为字符串。

```python
user_id = 12345
sender_id = str(user_id)  # "12345"

guild_id = None
guild_str = str(guild_id) if guild_id else None  # None
```

**为什么在本课中使用：** Discord 的用户 ID 和通道 ID 是整数，但消息总线的 `InboundMessage` 要求它们是字符串。用 `str()` 统一转换，确保不同平台的 ID 格式一致。

### 条件表达式（三元运算符）

Python 的三元运算符语法为 `值1 if 条件 else 值2`，在一行内完成条件判断。

```python
guild_id = message.guild.id if message.guild else None
name = user.first_name if user else "unknown"
```

**为什么在本课中使用：** Discord 消息可能来自私聊（无 guild）或服务器（有 guild）。用 `message.guild.id if message.guild else None` 在一行内处理两种情况，代码简洁。
