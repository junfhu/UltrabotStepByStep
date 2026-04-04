# Ultrabot：30 课程开发指南
**从零开始构建一个生产级 AI 助手框架。**
本指南将带你从"向 LLM 问好"一步步走到一个完整的多提供者、多通道 AI 智能体，具备工具调用、记忆、安全防护和 Web 界面。每节课程都建立在上一节课的基础之上。每节课都包含可运行的代码和测试。  
本教程的主要思路来自于
- Nanobot (https://github.com/HKUDS/nanobot)
- Learn-Claude-Code (https://github.com/shareAI-lab/learn-claude-code/)

本课程设计由AI辅助下完成，因为课程自身也在不停修正，请参考 https://github.com/junfhu/UltrabotStepByStep，如果您觉得对您有帮助，请帮助点亮一颗星。  
本课程中使用的大模型提供商是火山引擎Code Plan，如果正好你也需要，可以使用我的邀请码获取9折优惠 https://volcengine.com/L/_01BJCkKdMc/  邀请码：HHCDB4J4）  



# 课程 13：通道基类 + Telegram

**目标：** 定义所有消息通道的抽象基类，然后使用 `python-telegram-bot` 实现一个具体的 Telegram 通道。

**你将学到：**
- 包含 `start()`、`stop()`、`send()` 契约的 ABC 设计
- 出站发送的指数退避重试逻辑
- 用于生命周期管理的 `ChannelManager`
- 使用 `python-telegram-bot` 进行 Telegram 轮询
- 4096 字符的消息分块
- 将通道接入消息总线

**新建文件：**
- `ultrabot/channels/base.py` — `BaseChannel` ABC + `ChannelManager`
- `ultrabot/channels/telegram.py` — `TelegramChannel`

### 步骤 1：BaseChannel ABC

每个通道必须实现四项内容：`name`、`start()`、`stop()` 和
`send()`。基类提供重试逻辑和可选的输入指示器。

创建 `ultrabot/channels/base.py`：

```python
"""基础通道抽象和通道管理器。"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from ultrabot.bus.events import OutboundMessage
    from ultrabot.bus.queue import MessageBus


class BaseChannel(ABC):
    """所有消息通道的抽象基类。"""

    def __init__(self, config: dict, bus: "MessageBus") -> None:
        self.config = config
        self.bus = bus
        self._running = False

    @property
    @abstractmethod
    def name(self) -> str:
        """唯一标识符（例如 'telegram'、'discord'）。"""
        ...

    @abstractmethod
    async def start(self) -> None:
        """开始监听传入消息。"""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """优雅关闭。"""
        ...

    @abstractmethod
    async def send(self, message: "OutboundMessage") -> None:
        """向对应的聊天发送消息。"""
        ...

    async def send_with_retry(
        self,
        message: "OutboundMessage",
        max_retries: int = 3,
        base_delay: float = 1.0,
    ) -> None:
        """带指数退避的重试发送。"""
        last_exc: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                await self.send(message)
                return
            except Exception as exc:
                last_exc = exc
                if attempt < max_retries:
                    delay = base_delay * (2 ** (attempt - 1))
                    logger.warning(
                        "[{}] attempt {}/{} failed, retry in {:.1f}s: {}",
                        self.name, attempt, max_retries, delay, exc,
                    )
                    await asyncio.sleep(delay)
        logger.error("[{}] send failed after {} attempts", self.name, max_retries)
        raise last_exc  # type: ignore[misc]

    async def send_typing(self, chat_id: str | int) -> None:
        """发送输入指示器（默认为无操作）。"""
```

### 步骤 2：ChannelManager

```python
class ChannelManager:
    """消息通道的注册中心和生命周期管理器。"""

    def __init__(self, channels_config: dict, bus: "MessageBus") -> None:
        self.channels_config = channels_config
        self.bus = bus
        self._channels: dict[str, BaseChannel] = {}

    def register(self, channel: BaseChannel) -> None:
        self._channels[channel.name] = channel
        logger.info("Channel '{}' registered", channel.name)

    async def start_all(self) -> None:
        for name, channel in self._channels.items():
            ch_cfg = self.channels_config.get(name, {})
            if not ch_cfg.get("enabled", True):
                logger.info("Channel '{}' disabled — skipping", name)
                continue
            try:
                await channel.start()
                logger.info("Channel '{}' started", name)
            except Exception:
                logger.exception("Failed to start channel '{}'", name)

    async def stop_all(self) -> None:
        for name, channel in self._channels.items():
            try:
                await channel.stop()
            except Exception:
                logger.exception("Error stopping channel '{}'", name)

    def get_channel(self, name: str) -> BaseChannel | None:
        return self._channels.get(name)
```

### 步骤 3：TelegramChannel

创建 `ultrabot/channels/telegram.py`：

```python
"""使用 python-telegram-bot 的 Telegram 通道。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from loguru import logger
from ultrabot.channels.base import BaseChannel

if TYPE_CHECKING:
    from ultrabot.bus.events import OutboundMessage
    from ultrabot.bus.queue import MessageBus

try:
    from telegram import Update
    from telegram.ext import Application, ContextTypes, MessageHandler, filters
    _TELEGRAM_AVAILABLE = True
except ImportError:
    _TELEGRAM_AVAILABLE = False


def _require_telegram() -> None:
    if not _TELEGRAM_AVAILABLE:
        raise ImportError(
            "python-telegram-bot is required. "
            "Install: pip install 'ultrabot-ai[telegram]'"
        )


class TelegramChannel(BaseChannel):
    """Telegram 通道适配器。"""

    @property
    def name(self) -> str:
        return "telegram"

    def __init__(self, config: dict, bus: "MessageBus") -> None:
        _require_telegram()
        super().__init__(config, bus)
        self._token: str = config["token"]
        self._allow_from: list[int] | None = config.get("allowFrom")
        self._app: Any = None
```

### 步骤 4：处理传入消息

```python
    def _is_allowed(self, user_id: int) -> bool:
        if not self._allow_from:
            return True
        return user_id in self._allow_from

    async def _handle_message(
        self, update: "Update", context: "ContextTypes.DEFAULT_TYPE"
    ) -> None:
        """处理传入的 Telegram 消息。"""
        if update.message is None or update.message.text is None:
            return

        user = update.effective_user
        user_id = user.id if user else 0
        if not self._is_allowed(user_id):
            return

        from ultrabot.bus.events import InboundMessage

        inbound = InboundMessage(
            channel="telegram",
            sender_id=str(user_id),
            chat_id=str(update.message.chat_id),
            content=update.message.text,
            metadata={
                "user_name": user.first_name if user else "unknown",
            },
        )
        await self.bus.publish(inbound)
```

### 步骤 5：生命周期和出站

```python
    async def start(self) -> None:
        _require_telegram()
        builder = Application.builder().token(self._token)
        self._app = builder.build()
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message)
        )
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)
        self._running = True
        logger.info("Telegram channel started (polling)")

    async def stop(self) -> None:
        if self._app is not None:
            self._running = False
            if self._app.updater and self._app.updater.running:
                await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()

    async def send(self, message: "OutboundMessage") -> None:
        if self._app is None:
            raise RuntimeError("TelegramChannel not started")

        chat_id = int(message.chat_id)
        text = message.content

        # Telegram 限制为 4096 字符 — 必要时进行分块。
        max_len = 4096
        for i in range(0, len(text), max_len):
            await self._app.bot.send_message(
                chat_id=chat_id, text=text[i : i + max_len]
            )

    async def send_typing(self, chat_id: str | int) -> None:
        if self._app is None:
            return
        from telegram.constants import ChatAction
        await self._app.bot.send_chat_action(
            chat_id=int(chat_id), action=ChatAction.TYPING
        )
```

### 测试

```python
# tests/test_channels_base.py
import asyncio
from ultrabot.bus.events import InboundMessage, OutboundMessage
from ultrabot.bus.queue import MessageBus
from ultrabot.channels.base import BaseChannel, ChannelManager


class FakeChannel(BaseChannel):
    """用于测试的最小通道。"""

    @property
    def name(self) -> str:
        return "fake"

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def send(self, message: OutboundMessage) -> None:
        self.last_sent = message


def test_channel_manager_lifecycle():
    async def _run():
        bus = MessageBus()
        mgr = ChannelManager({"fake": {"enabled": True}}, bus)
        ch = FakeChannel({}, bus)
        mgr.register(ch)

        await mgr.start_all()
        assert ch._running is True

        await mgr.stop_all()
        assert ch._running is False

    asyncio.run(_run())


def test_send_with_retry():
    async def _run():
        bus = MessageBus()
        ch = FakeChannel({}, bus)
        msg = OutboundMessage(channel="fake", chat_id="1", content="hi")
        await ch.send_with_retry(msg)
        assert ch.last_sent.content == "hi"

    asyncio.run(_run())


def test_message_chunking_logic():
    """验证我们的分块方法对大消息有效。"""
    text = "A" * 10000
    max_len = 4096
    chunks = [text[i : i + max_len] for i in range(0, len(text), max_len)]
    assert len(chunks) == 3
    assert len(chunks[0]) == 4096
    assert len(chunks[2]) == 10000 - 2 * 4096
```

### 检查点

```bash
python -m pytest tests/test_channels_base.py -v
```

预期结果：全部 3 个测试通过。要进行 Telegram 实际测试，将你的机器人令牌添加到
配置中并运行网关 — 机器人应该会回复消息。

### 本课成果

一个定义了 `start/stop/send` 契约的 `BaseChannel` ABC，内置
指数退避重试；一个用于生命周期管理的 `ChannelManager`；以及一个
通过 `python-telegram-bot` 轮询消息并在 4096 字符 Telegram 限制处
分块出站消息的 `TelegramChannel`。

---

## 本课使用的 Python 知识

### `ABC` 和 `@abstractmethod`（抽象基类）

`ABC`（Abstract Base Class）是 Python 中定义"接口"的方式。继承 `ABC` 的类可以用 `@abstractmethod` 标记必须由子类实现的方法。如果子类没有实现所有抽象方法，实例化时会直接报错。

```python
from abc import ABC, abstractmethod

class Animal(ABC):
    @abstractmethod
    def speak(self) -> str:
        ...

class Dog(Animal):
    def speak(self) -> str:
        return "Woof!"

# animal = Animal()  # TypeError: 不能实例化抽象类
dog = Dog()           # OK，因为实现了 speak()
```

**为什么在本课中使用：** `BaseChannel` 定义了所有消息通道的契约（`name`、`start()`、`stop()`、`send()`）。任何新通道（Telegram、Discord、Slack 等）都必须实现这些方法，否则无法被实例化。这确保了所有通道具有一致的接口。

### `@property` + `@abstractmethod`（抽象属性）

`@property` 和 `@abstractmethod` 可以组合使用，要求子类必须定义某个属性。

```python
from abc import ABC, abstractmethod

class Channel(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        ...

class Telegram(Channel):
    @property
    def name(self) -> str:
        return "telegram"
```

**为什么在本课中使用：** `BaseChannel` 要求每个通道子类都提供一个 `name` 属性作为唯一标识符。使用抽象属性而不是普通属性，确保开发者不会忘记定义它。

### `TYPE_CHECKING` 条件导入

`typing.TYPE_CHECKING` 是一个在运行时为 `False`、但在类型检查工具（如 mypy）运行时为 `True` 的常量。配合 `if TYPE_CHECKING:` 使用，可以导入只用于类型注解的模块，避免运行时循环导入。

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from heavy_module import HeavyClass  # 只在类型检查时导入

def process(obj: "HeavyClass") -> None:  # 用字符串引用
    ...
```

**为什么在本课中使用：** `base.py` 中 `OutboundMessage` 和 `MessageBus` 只在类型注解中使用，运行时不需要。用 `TYPE_CHECKING` 避免在模块加载时导入它们，减少了模块间的耦合和循环依赖风险。

### `try / except ImportError`（可选依赖检测）

通过 `try/except ImportError` 来检测一个库是否安装，配合一个标志变量记录结果。这种模式让模块在依赖缺失时也能被导入，只在真正使用时才报错。

```python
try:
    import telegram
    _TELEGRAM_AVAILABLE = True
except ImportError:
    _TELEGRAM_AVAILABLE = False

def _require_telegram():
    if not _TELEGRAM_AVAILABLE:
        raise ImportError("请安装 python-telegram-bot")
```

**为什么在本课中使用：** `python-telegram-bot` 是可选依赖 — 不是所有用户都需要 Telegram 通道。通过这种模式，即使没安装该库，其他代码也能正常导入 `telegram.py` 模块，只有实际创建 `TelegramChannel` 实例时才会要求安装。

### 类继承和 `super().__init__()`

Python 通过 `class Child(Parent)` 语法实现继承。子类可以用 `super().__init__()` 调用父类的构造函数，确保父类的初始化逻辑被执行。

```python
class BaseChannel:
    def __init__(self, config, bus):
        self.config = config
        self.bus = bus

class TelegramChannel(BaseChannel):
    def __init__(self, config, bus):
        super().__init__(config, bus)  # 先初始化父类
        self._token = config["token"]  # 再初始化自己的属性
```

**为什么在本课中使用：** `TelegramChannel` 继承自 `BaseChannel`，需要先调用 `super().__init__(config, bus)` 初始化 `self.config`、`self.bus` 和 `self._running` 等基类属性，然后再设置 Telegram 特有的 `_token`、`_allow_from` 等属性。

### 指数退避重试（Exponential Backoff）

指数退避是一种重试策略：每次失败后等待时间翻倍。第 1 次等 1 秒，第 2 次等 2 秒，第 3 次等 4 秒……这样可以避免在服务暂时不可用时频繁重试造成雪崩。

```python
import asyncio

async def send_with_retry(max_retries=3, base_delay=1.0):
    for attempt in range(1, max_retries + 1):
        try:
            await do_send()
            return  # 成功就退出
        except Exception:
            if attempt < max_retries:
                delay = base_delay * (2 ** (attempt - 1))
                await asyncio.sleep(delay)  # 1s, 2s, 4s...
    raise Exception("All retries failed")
```

**为什么在本课中使用：** `BaseChannel.send_with_retry()` 实现了指数退避重试，用于出站消息发送。网络可能暂时不稳定，退避策略给服务器恢复的时间，而不是立即连续重试。

### `asyncio.sleep()`（异步延迟）

`asyncio.sleep()` 让当前协程暂停指定的秒数，期间不阻塞事件循环，其他协程可以继续运行。它跟 `time.sleep()` 的区别是后者会阻塞整个线程。

```python
import asyncio

async def delayed_greeting():
    print("请稍等...")
    await asyncio.sleep(2)  # 等2秒，但不阻塞其他任务
    print("你好！")
```

**为什么在本课中使用：** 指数退避重试中需要等待一段时间后再重试。使用 `asyncio.sleep()` 而不是 `time.sleep()`，确保等待期间消息总线和其他通道仍能正常工作。

### 字符串切片（String Slicing）

Python 字符串支持切片操作 `text[start:end]`，可以提取子字符串。配合 `range(0, len(text), step)` 可以把长字符串按固定长度分块。

```python
text = "ABCDEFGHIJ"
chunk1 = text[0:4]   # "ABCD"
chunk2 = text[4:8]   # "EFGH"
chunk3 = text[8:12]  # "IJ"（超出范围不会报错）

# 通用的分块写法：
max_len = 4
chunks = [text[i : i + max_len] for i in range(0, len(text), max_len)]
```

**为什么在本课中使用：** Telegram 限制单条消息最长 4096 字符。`send()` 方法用 `text[i : i + max_len]` 把长消息分成多个块逐一发送，确保不超过平台限制。

### `range(start, stop, step)` 带步长的范围

`range()` 可以接受第三个参数作为步长，用来生成间隔固定的数字序列。

```python
for i in range(0, 100, 25):
    print(i)  # 输出: 0, 25, 50, 75
```

**为什么在本课中使用：** `range(0, len(text), max_len)` 生成 `0, 4096, 8192, ...` 的序列，作为每个消息块的起始位置，实现了把任意长度文本按 4096 字符分块。

### `dict.get(key, default)`（安全字典访问）

`dict.get()` 在键不存在时返回默认值（默认为 `None`），而不是抛出 `KeyError`。

```python
config = {"token": "abc123"}
token = config["token"]          # "abc123"
allow = config.get("allowFrom")  # None（键不存在，不报错）
port = config.get("port", 8080)  # 8080（键不存在，返回默认值）
```

**为什么在本课中使用：** 通道配置中某些字段是可选的（如 `allowFrom`），使用 `config.get("allowFrom")` 在字段缺失时返回 `None` 而不是崩溃。`ChannelManager` 中也用 `ch_cfg.get("enabled", True)` 来获取可选的 enabled 标志。

### 函数内延迟导入（Lazy Import）

在函数体内部使用 `import` 语句，而不是在模块顶部。这样模块只在函数被调用时才被加载。

```python
async def _handle_message(self, update, context):
    from ultrabot.bus.events import InboundMessage  # 用到时才导入
    inbound = InboundMessage(...)
```

**为什么在本课中使用：** `_handle_message` 方法在函数内部导入 `InboundMessage`，避免了模块顶层的循环依赖。Telegram 模块在导入时不需要加载整个消息总线，只有在实际处理消息时才按需导入。

### `raise` 抛出异常

`raise` 用于主动抛出一个异常。可以抛出内置异常或自定义异常。

```python
def connect():
    if not ready:
        raise RuntimeError("Service not started")
```

**为什么在本课中使用：** `send()` 方法在 `_app` 为 `None` 时抛出 `RuntimeError`，强制要求先调用 `start()` 才能发送消息。`_require_telegram()` 在库缺失时抛出 `ImportError`，给用户清晰的安装提示。

### `typing.Any`（任意类型）

`Any` 类型表示"任何类型都行"，相当于关闭了对该变量的类型检查。当精确类型不重要或难以表达时使用。

```python
from typing import Any

self._app: Any = None  # 类型太复杂或来自外部库，先用 Any
```

**为什么在本课中使用：** `_app` 是 `telegram.ext.Application` 类型，它的泛型参数复杂，且只在 `python-telegram-bot` 安装时才可用。使用 `Any` 既避免了硬依赖，又能让代码通过类型检查。

### `str | int` 联合类型

表示一个值可以是多种类型之一。`str | int` 表示"字符串或整数"。

```python
async def send_typing(self, chat_id: str | int) -> None:
    await bot.send_chat_action(chat_id=int(chat_id), ...)
```

**为什么在本课中使用：** `send_typing()` 的 `chat_id` 参数可能从外部以字符串或整数形式传入，使用 `str | int` 联合类型让方法更灵活，内部统一转换为 `int` 后传给 Telegram API。

### `ChannelManager` 注册中心模式

注册中心模式是一种管理多个同类型组件的设计模式：提供 `register()`、`start_all()`、`stop_all()` 等方法统一管理生命周期。

```python
class ChannelManager:
    def __init__(self):
        self._channels: dict[str, BaseChannel] = {}

    def register(self, channel: BaseChannel) -> None:
        self._channels[channel.name] = channel

    async def start_all(self) -> None:
        for name, channel in self._channels.items():
            await channel.start()
```

**为什么在本课中使用：** 系统可能有多个通道（Telegram、Discord、Slack）。`ChannelManager` 统一管理它们的注册和生命周期，启动时逐一启动，关闭时逐一停止，并处理各通道的异常。

### 测试中的假对象（Test Double / Fake）

在测试中创建一个继承自抽象基类的简化实现，用于验证基类的逻辑而不依赖真实的外部服务。

```python
class FakeChannel(BaseChannel):
    @property
    def name(self) -> str:
        return "fake"

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def send(self, message) -> None:
        self.last_sent = message  # 记录下来供断言检查
```

**为什么在本课中使用：** 测试不需要真正连接 Telegram 服务器。`FakeChannel` 实现了 `BaseChannel` 的所有抽象方法，让我们可以测试 `ChannelManager` 的生命周期管理和 `send_with_retry()` 的重试逻辑。

### `from __future__ import annotations`（延迟注解评估）

让类型注解在运行时不被求值，支持前向引用和新语法。

```python
from __future__ import annotations

class BaseChannel(ABC):
    async def send(self, message: "OutboundMessage") -> None:  # 字符串引用
        ...
```

**为什么在本课中使用：** 代码中引用了 `OutboundMessage` 和 `MessageBus` 等类型，但它们在运行时可能还未导入（因为使用了 `TYPE_CHECKING` 条件导入）。延迟注解确保这些字符串形式的类型引用不会在运行时报错。
