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
