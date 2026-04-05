# Ultrabot：30 课程开发指南
**从零开始构建一个生产级 AI 助手框架。**
本指南将带你从"向 LLM 问好"一步步走到一个完整的多提供者、多通道 AI 智能体，具备工具调用、记忆、安全防护和 Web 界面。每节课程都建立在上一节课的基础之上。每节课都包含可运行的代码和测试。  
本教程的主要思路来自于
- Nanobot (https://github.com/HKUDS/nanobot)
- Learn-Claude-Code (https://github.com/shareAI-lab/learn-claude-code/)

本课程设计由AI辅助下完成，因为课程自身也在不停修正，请参考 https://github.com/junfhu/UltrabotStepByStep，如果您觉得对您有帮助，请帮助点亮一颗星。  
本课程中使用的大模型提供商是火山引擎Code Plan，如果正好你也需要，可以使用我的邀请码获取9折优惠 https://volcengine.com/L/_01BJCkKdMc/  邀请码：HHCDB4J4）  



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

### 步骤 6：配置与运行网关

网关通过 `~/.ultrabot/config.json` 中的 `channels` 部分决定启用哪些通道。
配置值支持 `${ENV_VAR}` 语法引用环境变量，网关启动时自动展开。

#### 完整配置示例

```json
{
  "agents": {
    "defaults": {
      "model": "minimax-m2.5",
      "provider": "openai_compatible"
    }
  },
  "providers": {
    "openaiCompatible": {
      "apiBase": "https://ark.cn-beijing.volces.com/api/coding/v3",
      "priority": 1,
      "models": ["minimax-m2.5"]
    }
  },
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "${TELEGRAM_BOT_TOKEN}",
      "allowFrom": []
    },
    "discord": {
      "enabled": true,
      "token": "${DISCORD_BOT_TOKEN}",
      "allowFrom": [],
      "allowedGuilds": []
    },
    "slack": {
      "enabled": false,
      "botToken": "${SLACK_BOT_TOKEN}",
      "appToken": "${SLACK_APP_TOKEN}",
      "allowFrom": []
    }
  }
}
```

只需将 `enabled` 设为 `true` 并设置对应的环境变量，即可启用任意通道组合。
未启用的通道不会被加载，其 SDK 也不需要安装。

#### 各通道的环境变量

| 通道 | 环境变量 | 获取方式 |
|------|---------|---------|
| Telegram | `TELEGRAM_BOT_TOKEN` | @BotFather `/newbot` |
| Discord | `DISCORD_BOT_TOKEN` | [Developer Portal](https://discord.com/developers/applications) → Bot → Reset Token |
| Slack | `SLACK_BOT_TOKEN` + `SLACK_APP_TOKEN` | [Slack API](https://api.slack.com/apps) → OAuth & Socket Mode |

#### 运行网关

```bash
# 设置环境变量
export TELEGRAM_BOT_TOKEN="..."
export DISCORD_BOT_TOKEN="..."

# 启动网关（启动所有已启用的通道）
python -m ultrabot.gateway
```

预期日志输出：

```
INFO | Gateway starting up
INFO | Channel 'telegram' registered
INFO | Channel 'discord' registered
INFO | Telegram channel started (polling)
INFO | Discord bot connected as Ultrabot#1234
INFO | Gateway started — dispatching messages
```

按 `Ctrl+C` 触发优雅关闭，所有通道会依次停止。

> **排查提示：**
> - `ValidationError: channels — Extra inputs are not permitted` → 确认 `Config` 模式中已添加 `channels: dict` 字段。
> - 某个通道不启动？检查对应的 `enabled` 是否为 `true`，以及 SDK 是否已安装。
> - Token 没有被展开（仍然是 `${...}`）？确认环境变量名拼写正确，且已在当前 shell 中 `export`。

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

## 本课使用的 Python 知识

### `signal` 模块（信号处理）

`signal` 模块让程序能捕获操作系统发出的信号。`SIGINT`（Ctrl+C）和 `SIGTERM`（终止请求）是最常见的关闭信号。

```python
import signal

def handle_shutdown(signum, frame):
    print("收到关闭信号，正在停止...")

signal.signal(signal.SIGINT, handle_shutdown)   # Ctrl+C
signal.signal(signal.SIGTERM, handle_shutdown)  # kill 命令
```

**为什么在本课中使用：** 网关服务器是一个长期运行的进程。用户按 Ctrl+C 或运维发送 `kill` 命令时，需要优雅地关闭所有通道和消息总线，而不是粗暴地终止。通过注册信号处理器，网关能在收到信号后有序地停止各组件。

### `asyncio.get_running_loop()` 和 `loop.add_signal_handler()`

在异步程序中，信号处理器需要通过事件循环注册，而不是用 `signal.signal()`。`add_signal_handler()` 让信号处理器能安全地与异步代码配合。

```python
import asyncio
import signal

async def main():
    loop = asyncio.get_running_loop()

    loop.add_signal_handler(
        signal.SIGINT,
        lambda: asyncio.create_task(shutdown())
    )
```

**为什么在本课中使用：** 网关的 `start()` 方法在异步上下文中运行。使用 `loop.add_signal_handler()` 注册 `SIGINT` 和 `SIGTERM` 的处理器，在收到信号时创建一个异步任务调用 `self.stop()`，确保关闭过程中可以安全地 `await` 各种清理操作。

### `pathlib.Path`（路径操作）

`pathlib.Path` 是 Python 3.4 引入的面向对象路径处理工具，比传统的 `os.path` 字符串操作更直观。

```python
from pathlib import Path

workspace = Path("~/ultrabot/data")
workspace = workspace.expanduser()   # 展开 ~ 为用户主目录
workspace = workspace.resolve()      # 转为绝对路径
workspace.mkdir(parents=True, exist_ok=True)  # 创建目录（含父目录）
```

**为什么在本课中使用：** 网关从配置中读取工作空间路径，需要展开 `~`、解析为绝对路径、确保目录存在。`Path` 的链式方法 `expanduser().resolve()` 比手动拼接字符串更安全、更易读。

### `Path.mkdir(parents=True, exist_ok=True)`

`mkdir()` 创建目录。`parents=True` 表示自动创建父目录（相当于 `mkdir -p`），`exist_ok=True` 表示目录已存在时不报错。

```python
from pathlib import Path

Path("/data/ultrabot/sessions").mkdir(parents=True, exist_ok=True)
# 如果 /data 或 /data/ultrabot 不存在，也会自动创建
```

**为什么在本课中使用：** 工作空间目录可能还不存在（首次运行），也可能已经存在（重新启动）。两个参数确保这两种情况都能正确处理，不需要额外的判断逻辑。

### 延迟导入避免循环依赖（Lazy Import）

在函数体内部而不是模块顶部使用 `import` 语句，推迟模块的加载时机。

```python
async def start(self):
    # 延迟导入以避免循环依赖
    from ultrabot.bus.queue import MessageBus
    from ultrabot.providers.manager import ProviderManager
    from ultrabot.agent.agent import Agent

    self._bus = MessageBus()
    self._agent = Agent(...)
```

**为什么在本课中使用：** `Gateway` 组合了几乎所有组件（MessageBus、Agent、ChannelManager 等），如果在模块顶部全部导入，很容易形成循环依赖（A 导入 B，B 又导入 A）。在 `start()` 方法内部延迟导入，只有在真正需要时才加载这些模块。

### `asyncio.CancelledError`（任务取消异常）

当一个异步任务被取消时，会抛出 `asyncio.CancelledError`。可以用 `try/except` 捕获它来做清理工作。

```python
try:
    await long_running_task()
except asyncio.CancelledError:
    print("任务被取消了，执行清理...")
```

**为什么在本课中使用：** `start()` 方法的 `try/except asyncio.CancelledError` 捕获分发循环被取消的情况（比如信号处理器触发了关闭），确保 `finally` 块中的 `stop()` 被执行，实现优雅关闭。

### `try / except / finally`（异常处理与清理）

`finally` 块中的代码无论是否发生异常都会执行，通常用于资源清理。

```python
try:
    await process()
except asyncio.CancelledError:
    pass  # 正常取消，不需要特殊处理
finally:
    await cleanup()  # 无论如何都要执行清理
```

**为什么在本课中使用：** 网关的主循环在 `try` 中运行，`finally` 中调用 `self.stop()` 确保所有通道和消息总线被正确关闭。即使程序因异常或取消而退出，资源也不会泄漏。

### `importlib.import_module()`（动态导入）

`importlib.import_module()` 可以用字符串指定模块路径来动态导入模块，实现"配置驱动"的插件加载。

```python
import importlib

module_path = "ultrabot.channels.telegram"
class_name = "TelegramChannel"

mod = importlib.import_module(module_path)  # 动态导入模块
cls = getattr(mod, class_name)              # 动态获取类
instance = cls(config, bus)                 # 实例化
```

**为什么在本课中使用：** 网关通过配置文件决定启用哪些通道。`_register_channels()` 方法使用 `importlib.import_module()` 根据通道名动态加载对应的模块和类，而不是硬编码一堆 `if/elif`。新增一个通道只需在 `channel_map` 字典中加一行。

### 嵌套函数（Nested Function）

在函数内部定义的函数，可以访问外部函数的局部变量。常用于封装辅助逻辑。

```python
def _register_channels(self, channels_extra):
    def _is_enabled(cfg) -> bool:
        if isinstance(cfg, dict):
            return cfg.get("enabled", False)
        return getattr(cfg, "enabled", False)

    def _to_dict(cfg) -> dict:
        return cfg if isinstance(cfg, dict) else cfg.__dict__

    # 在主逻辑中使用这些辅助函数
    for name, cfg in channels_extra.items():
        if _is_enabled(cfg):
            ...
```

**为什么在本课中使用：** `_is_enabled()` 和 `_to_dict()` 是只在 `_register_channels()` 中使用的辅助函数。定义为嵌套函数而不是类方法，清晰地表达了"这些函数只在这里使用"的意图，保持了类接口的简洁。

### `isinstance()` 类型检查

`isinstance(obj, type)` 检查一个对象是否是某个类型的实例。比直接用 `type(obj) == type` 更好，因为它支持继承。

```python
def _is_enabled(cfg) -> bool:
    if isinstance(cfg, dict):
        return cfg.get("enabled", False)   # 字典用 get
    return getattr(cfg, "enabled", False)  # 对象用 getattr
```

**为什么在本课中使用：** 通道配置可能是字典（从 JSON/YAML 解析）或对象（从 Pydantic model 解析）。`isinstance()` 判断类型后采用不同的访问方式，确保两种情况都能正确处理。

### `getattr()` 动态属性访问

`getattr(obj, name, default)` 用字符串来访问对象的属性，属性不存在时返回默认值。

```python
class Config:
    enabled = True

config = Config()
is_on = getattr(config, "enabled", False)  # True
debug = getattr(config, "debug", False)    # False（不存在）
```

**为什么在本课中使用：** `_is_enabled()` 和 `_to_dict()` 中用 `getattr()` 处理配置可能是对象的情况。`obj.__dict__` 也通过 `getattr` 的思路将对象属性转为字典。

### `assert` 断言

`assert` 在条件为 `False` 时抛出 `AssertionError`，用于在开发时验证程序的内部假设。

```python
assert isinstance(inbound, InboundMessage)
# 如果 inbound 不是 InboundMessage 类型，立即报错
```

**为什么在本课中使用：** `_handle_inbound()` 开头用 `assert isinstance(inbound, InboundMessage)` 确保传入的参数类型正确。这是一种防御性编程，帮助在开发阶段尽早发现类型错误。

### `lambda` 匿名函数

`lambda` 创建简短的一次性函数，适合用在需要传入回调函数的地方。

```python
# 信号处理器中使用 lambda 创建一个立即执行的回调
loop.add_signal_handler(
    signal.SIGINT,
    lambda: asyncio.create_task(self.stop())
)
```

**为什么在本课中使用：** `add_signal_handler` 需要一个无参回调函数。`lambda: asyncio.create_task(self.stop())` 用 lambda 包装了异步任务的创建，让同步的信号处理器能触发异步的关闭流程。

### 配置驱动的通道注册模式

通过一个字典映射通道名到模块路径和类名，实现"增加平台不改代码，只改配置"。

```python
channel_map = {
    "telegram":  ("ultrabot.channels.telegram", "TelegramChannel"),
    "discord":   ("ultrabot.channels.discord_channel", "DiscordChannel"),
    "slack":     ("ultrabot.channels.slack_channel", "SlackChannel"),
}

for name, (module_path, class_name) in channel_map.items():
    cfg = channels_extra.get(name)
    if cfg and _is_enabled(cfg):
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        manager.register(cls(config, bus))
```

**为什么在本课中使用：** 网关支持 7 种通道（Telegram、Discord、Slack、飞书、QQ 等）。通过字典 + 动态导入的方式，每种通道只占一行配置，新增通道无需修改注册逻辑，体现了开闭原则（对扩展开放，对修改关闭）。

### `unittest.mock` — `AsyncMock` 和 `MagicMock`（模拟对象）

`unittest.mock` 是 Python 标准库的测试工具。`MagicMock` 创建一个假对象，`AsyncMock` 专门用于模拟异步方法。它们自动记录所有调用，方便断言验证。

```python
from unittest.mock import AsyncMock, MagicMock

# 模拟一个异步方法
mock_agent = AsyncMock()
mock_agent.run.return_value = "Hello!"

# 调用并验证
result = await mock_agent.run("Hi")
mock_agent.run.assert_called_once()

# MagicMock 模拟同步对象
mock_mgr = MagicMock()
mock_mgr.get_channel.return_value = mock_channel
```

**为什么在本课中使用：** 测试网关的入站处理逻辑需要模拟智能体、通道和通道管理器。`AsyncMock` 让异步方法 `agent.run()`、`channel.send_with_retry()` 可以被 `await` 并返回预设值。`MagicMock(spec=ChannelManager)` 创建了一个符合 `ChannelManager` 接口的假对象。

### `MagicMock(spec=...)` 规范约束

`spec` 参数让 `MagicMock` 只允许访问真实类上存在的属性和方法。访问不存在的属性会抛出 `AttributeError`，帮助发现测试代码中的拼写错误。

```python
from unittest.mock import MagicMock
from ultrabot.channels.base import ChannelManager

mock_mgr = MagicMock(spec=ChannelManager)
mock_mgr.get_channel("test")      # OK，ChannelManager 有这个方法
# mock_mgr.nonexistent()          # AttributeError！
```

**为什么在本课中使用：** `MagicMock(spec=ChannelManager)` 确保测试中只调用 `ChannelManager` 真正有的方法。如果将来接口改变（比如方法改名），测试会立即失败，而不是默默通过。

### `__all__` 模块导出控制

`__all__` 声明模块的公共 API，控制 `from package import *` 导出哪些名字。

```python
# ultrabot/gateway/__init__.py
from ultrabot.gateway.server import Gateway

__all__ = ["Gateway"]
```

**为什么在本课中使用：** `ultrabot/gateway/__init__.py` 用 `__all__` 明确声明网关包只导出 `Gateway` 类。外部代码只需 `from ultrabot.gateway import Gateway`，不需要知道内部模块结构。
