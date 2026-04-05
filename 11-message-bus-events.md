# Ultrabot：30 课程开发指南
**从零开始构建一个生产级 AI 助手框架。**
本指南将带你从"向 LLM 问好"一步步走到一个完整的多提供者、多通道 AI 智能体，具备工具调用、记忆、安全防护和 Web 界面。每节课程都建立在上一节课的基础之上。每节课都包含可运行的代码和测试。  
本教程的主要思路来自于
- Nanobot (https://github.com/HKUDS/nanobot)
- Learn-Claude-Code (https://github.com/shareAI-lab/learn-claude-code/)

本课程设计由AI辅助下完成，因为课程自身也在不停修正，请参考 https://github.com/junfhu/UltrabotStepByStep，如果您觉得对您有帮助，请帮助点亮一颗星。  
本课程中使用的大模型提供商是火山引擎Code Plan，如果正好你也需要，可以使用我的邀请码获取9折优惠 https://volcengine.com/L/_01BJCkKdMc/  邀请码：HHCDB4J4）  



# 课程 11：消息总线 + 事件

**目标：** 通过基于优先级的异步消息总线，将消息生产者（通道）与消费者（智能体）解耦。

**你将学到：**
- 设计 `InboundMessage` 和 `OutboundMessage` 数据类
- 使用自定义排序的 `asyncio.PriorityQueue`
- 出站消息的扇出（fan-out）模式
- 用于重试耗尽的消息的死信队列
- 使用 `asyncio.Event` 实现优雅关闭
- 编写端到端集成测试验证完整消息链路

**新建文件：**
- `ultrabot/bus/__init__.py` — 公共重导出
- `ultrabot/bus/events.py` — `InboundMessage` 和 `OutboundMessage` 数据类
- `ultrabot/bus/queue.py` — 带优先级队列的 `MessageBus`
- `tests/test_bus_integration.py` — 端到端集成测试（多通道路由、优先级调度）
- `tests/test_bus_real_agent.py` — 真实 Agent 端到端测试（可选，需 API Key）

### 步骤 1：消息数据类

系统中流转的每条消息都是一个简单的数据类。入站消息
携带通道元数据；出站消息指向特定的通道和聊天。

创建 `ultrabot/bus/events.py`：

```python
"""消息总线上入站和出站消息的数据类定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class InboundMessage:
    """从任何通道接收的、进入处理管道的消息。

    ``priority`` 字段控制处理顺序：数字越大
    越先被处理（类似 VIP 通道）。
    """

    channel: str                          # 例如 "telegram"、"discord"
    sender_id: str                        # 唯一发送者标识
    chat_id: str                          # 对话标识
    content: str                          # 原始文本内容
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    media: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    session_key_override: str | None = None
    priority: int = 0                     # 0 = 普通；数值越高 = 越快

    @property
    def session_key(self) -> str:
        """推导会话密钥：使用覆盖值或 ``{channel}:{chat_id}``。"""
        if self.session_key_override is not None:
            return self.session_key_override
        return f"{self.channel}:{self.chat_id}"

    def __lt__(self, other: InboundMessage) -> bool:
        """高优先级在最小堆中被视为"小于"。

        ``asyncio.PriorityQueue`` 是最小堆，所以我们反转比较：
        priority=10 的消息"小于" priority=0 的消息，
        从而使其优先出队。
        """
        if not isinstance(other, InboundMessage):
            return NotImplemented
        return self.priority > other.priority


@dataclass
class OutboundMessage:
    """要通过通道适配器发送出去的消息。"""

    channel: str
    chat_id: str
    content: str
    reply_to: str | None = None
    media: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
```

**关键设计决策：** `__lt__` 的反转。Python 的 `heapq`（被
`PriorityQueue` 使用）是一个*最小*堆。我们希望高优先级消息先出队，
因此翻转了比较逻辑。

### 步骤 2：MessageBus

创建 `ultrabot/bus/queue.py`：

```python
"""基于优先级的异步消息总线。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

from loguru import logger
from ultrabot.bus.events import InboundMessage, OutboundMessage

# 处理器签名的类型别名。
InboundHandler = Callable[
    [InboundMessage], Coroutine[Any, Any, OutboundMessage | None]
]
OutboundSubscriber = Callable[
    [OutboundMessage], Coroutine[Any, Any, None]
]


class MessageBus:
    """带有优先级入站队列和扇出出站分发的中央总线。

    Parameters:
        max_retries:   发送到死信队列之前的尝试次数。
        queue_maxsize: 入站队列的上限（0 = 无限制）。
    """

    def __init__(self, max_retries: int = 3, queue_maxsize: int = 0) -> None:
        self.max_retries = max_retries

        # 入站优先级队列 — 排序使用 InboundMessage.__lt__。
        self._inbound_queue: asyncio.PriorityQueue[InboundMessage] = (
            asyncio.PriorityQueue(maxsize=queue_maxsize)
        )
        self._inbound_handler: InboundHandler | None = None
        self._outbound_subscribers: list[OutboundSubscriber] = []
        self.dead_letter_queue: list[InboundMessage] = []
        self._shutdown_event = asyncio.Event()
```

### 步骤 3：发布和分发

```python
    async def publish(self, message: InboundMessage) -> None:
        """将入站消息加入队列等待处理。"""
        await self._inbound_queue.put(message)
        logger.debug(
            "Published | channel={} chat_id={} priority={}",
            message.channel, message.chat_id, message.priority,
        )

    def set_inbound_handler(self, handler: InboundHandler) -> None:
        """注册处理每条入站消息的处理器。"""
        self._inbound_handler = handler

    async def dispatch_inbound(self) -> None:
        """长期运行的循环：拉取消息并处理。

        运行直到 shutdown() 被调用。失败的消息会被重试
        最多 max_retries 次；之后进入 dead_letter_queue。
        """
        logger.info("Inbound dispatch loop started")

        while not self._shutdown_event.is_set():
            try:
                message = await asyncio.wait_for(
                    self._inbound_queue.get(), timeout=1.0,
                )
            except asyncio.TimeoutError:
                continue                          # 检查关闭标志

            if self._inbound_handler is None:
                logger.warning("No handler registered — message dropped")
                self._inbound_queue.task_done()
                continue

            await self._process_with_retries(message)
            self._inbound_queue.task_done()

        logger.info("Inbound dispatch loop stopped")

    async def _process_with_retries(self, message: InboundMessage) -> None:
        """带重试的处理尝试；重试耗尽后进入死信队列。"""
        for attempt in range(1, self.max_retries + 1):
            try:
                result = await self._inbound_handler(message)
                if result is not None:
                    await self.send_outbound(result)
                return
            except Exception:
                logger.exception(
                    "Error processing (attempt {}/{}) | session_key={}",
                    attempt, self.max_retries, message.session_key,
                )
        # 所有重试已耗尽。
        self.dead_letter_queue.append(message)
        logger.error(
            "Dead-lettered after {} retries | session_key={}",
            self.max_retries, message.session_key,
        )
```

### 步骤 4：出站扇出

多个通道可以订阅出站消息。每个订阅者
接收每条出站消息，并决定是否处理它（通常
通过检查 `message.channel`）。

```python
    def subscribe(self, handler: OutboundSubscriber) -> None:
        """注册一个出站订阅者。"""
        self._outbound_subscribers.append(handler)

    async def send_outbound(self, message: OutboundMessage) -> None:
        """扇出到所有已注册的出站订阅者。"""
        for subscriber in self._outbound_subscribers:
            try:
                await subscriber(message)
            except Exception:
                logger.exception("Outbound subscriber failed")

    def shutdown(self) -> None:
        """通知分发循环停止。"""
        self._shutdown_event.set()

    @property
    def inbound_queue_size(self) -> int:
        return self._inbound_queue.qsize()

    @property
    def dead_letter_count(self) -> int:
        return len(self.dead_letter_queue)
```

### 步骤 5：包初始化

创建 `ultrabot/bus/__init__.py`：

```python
"""消息总线包的公共 API。"""

from ultrabot.bus.events import InboundMessage, OutboundMessage
from ultrabot.bus.queue import MessageBus

__all__ = ["InboundMessage", "MessageBus", "OutboundMessage"]
```

### 测试

```python
# tests/test_bus.py
import asyncio
from ultrabot.bus.events import InboundMessage, OutboundMessage
from ultrabot.bus.queue import MessageBus


def test_priority_ordering():
    """高优先级消息应被视为"小于"。"""
    low = InboundMessage(channel="t", sender_id="1", chat_id="1",
                         content="low", priority=0)
    high = InboundMessage(channel="t", sender_id="1", chat_id="1",
                          content="high", priority=10)
    assert high < low  # 高优先级在最小堆中"小于"

def test_session_key_derivation():
    msg = InboundMessage(channel="telegram", sender_id="u1",
                         chat_id="c1", content="hi")
    assert msg.session_key == "telegram:c1"

    msg2 = InboundMessage(channel="telegram", sender_id="u1",
                          chat_id="c1", content="hi",
                          session_key_override="custom-key")
    assert msg2.session_key == "custom-key"


def test_bus_dispatch_and_dead_letter():
    async def _run():
        bus = MessageBus(max_retries=2)

        # 始终失败的处理器。
        async def bad_handler(msg):
            raise ValueError("boom")

        bus.set_inbound_handler(bad_handler)

        msg = InboundMessage(channel="test", sender_id="1",
                             chat_id="1", content="hello")
        await bus.publish(msg)

        # 运行分发循环一小段时间。
        task = asyncio.create_task(bus.dispatch_inbound())
        await asyncio.sleep(0.5)
        bus.shutdown()
        await task

        # 消息应该在死信队列中。
        assert bus.dead_letter_count == 1

    asyncio.run(_run())


def test_bus_outbound_fanout():
    async def _run():
        bus = MessageBus()
        received = []

        async def subscriber(msg):
            received.append(msg.content)

        bus.subscribe(subscriber)
        bus.subscribe(subscriber)  # 两个订阅者

        out = OutboundMessage(channel="test", chat_id="1", content="reply")
        await bus.send_outbound(out)

        assert received == ["reply", "reply"]  # 两个都收到了

    asyncio.run(_run())
```

### 步骤 6：集成测试 — 端到端消息流

前面的单元测试验证了总线的各个零件。现在我们把它们串起来，
模拟真实场景：**入站消息 → 处理器 → 出站回复**。

创建 `tests/test_bus_integration.py`：

```python
# tests/test_bus_integration.py
"""端到端集成测试：验证消息从入站到出站的完整流转。"""
import asyncio
from ultrabot.bus.events import InboundMessage, OutboundMessage
from ultrabot.bus.queue import MessageBus


def test_end_to_end_message_flow():
    """模拟真实消息流：入站 → 处理 → 出站。"""
    async def _run():
        bus = MessageBus()
        delivered: list[OutboundMessage] = []

        # 模拟 Agent 处理：接收入站消息，返回出站回复
        async def agent_handler(msg: InboundMessage) -> OutboundMessage:
            reply_text = f"Echo: {msg.content}"
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=reply_text,
            )

        # 模拟通道适配器：收集出站消息
        async def channel_sender(msg: OutboundMessage):
            delivered.append(msg)

        bus.set_inbound_handler(agent_handler)
        bus.subscribe(channel_sender)

        # 发布一条来自 Telegram 用户的消息
        await bus.publish(InboundMessage(
            channel="telegram", sender_id="user_123",
            chat_id="chat_456", content="你好，Ultrabot！",
        ))

        # 启动分发循环
        task = asyncio.create_task(bus.dispatch_inbound())
        await asyncio.sleep(0.3)
        bus.shutdown()
        await task

        # 验证出站消息
        assert len(delivered) == 1
        assert delivered[0].channel == "telegram"
        assert delivered[0].chat_id == "chat_456"
        assert delivered[0].content == "Echo: 你好，Ultrabot！"

    asyncio.run(_run())


def test_multi_channel_routing():
    """多通道消息应独立路由到各自的出站订阅者。"""
    async def _run():
        bus = MessageBus()
        telegram_out: list[OutboundMessage] = []
        discord_out: list[OutboundMessage] = []

        async def agent_handler(msg: InboundMessage) -> OutboundMessage:
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=f"[{msg.channel}] {msg.content}",
            )

        # 两个订阅者按通道过滤
        async def telegram_sender(msg: OutboundMessage):
            if msg.channel == "telegram":
                telegram_out.append(msg)

        async def discord_sender(msg: OutboundMessage):
            if msg.channel == "discord":
                discord_out.append(msg)

        bus.set_inbound_handler(agent_handler)
        bus.subscribe(telegram_sender)
        bus.subscribe(discord_sender)

        # 发布两条来自不同通道的消息
        await bus.publish(InboundMessage(
            channel="telegram", sender_id="u1",
            chat_id="tg_1", content="来自 Telegram",
        ))
        await bus.publish(InboundMessage(
            channel="discord", sender_id="u2",
            chat_id="dc_1", content="来自 Discord",
        ))

        task = asyncio.create_task(bus.dispatch_inbound())
        await asyncio.sleep(0.5)
        bus.shutdown()
        await task

        assert len(telegram_out) == 1
        assert telegram_out[0].content == "[telegram] 来自 Telegram"
        assert len(discord_out) == 1
        assert discord_out[0].content == "[discord] 来自 Discord"

    asyncio.run(_run())


def test_priority_dispatch_order():
    """高优先级消息应先被处理，即使后发布。"""
    async def _run():
        bus = MessageBus()
        order: list[str] = []

        async def handler(msg: InboundMessage) -> OutboundMessage | None:
            order.append(msg.content)
            return None  # 不生成出站消息

        bus.set_inbound_handler(handler)

        # 先发普通消息，再发 VIP 消息
        await bus.publish(InboundMessage(
            channel="t", sender_id="1", chat_id="1",
            content="normal", priority=0,
        ))
        await bus.publish(InboundMessage(
            channel="t", sender_id="2", chat_id="2",
            content="vip", priority=10,
        ))

        task = asyncio.create_task(bus.dispatch_inbound())
        await asyncio.sleep(0.3)
        bus.shutdown()
        await task

        # VIP 应排在第一位
        assert order == ["vip", "normal"]

    asyncio.run(_run())
```

这三个测试覆盖了三个核心场景：

| 测试 | 验证点 |
|---|---|
| `test_end_to_end_message_flow` | 完整链路：发布 → 处理器 → 出站订阅者 |
| `test_multi_channel_routing` | 多通道消息各自路由到对应的订阅者 |
| `test_priority_dispatch_order` | 优先级队列确保 VIP 消息先被处理 |

### 步骤 7：真实 Agent 端到端测试（可选）

如果你已经配置了 LLM API 密钥，可以进一步测试**真正调用大模型**的完整链路。

凭据解析优先级：
1. 环境变量 `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL`
2. `~/.ultrabot/config.json` 中的默认提供者配置（`agents.defaults.provider`）

两者都没有时自动跳过。

创建 `tests/test_bus_real_agent.py`：

```python
# tests/test_bus_real_agent.py
"""端到端真实测试：入站消息 → MessageBus → Agent(LLM) → 出站回复。

凭据解析优先级：
  1. 环境变量 OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL
  2. ~/.ultrabot/config.json 中的默认提供者配置

运行：
  pytest tests/test_bus_real_agent.py -v -s
"""
import asyncio
import os

import pytest
from openai import OpenAI

from ultrabot.agent import Agent
from ultrabot.bus.events import InboundMessage, OutboundMessage
from ultrabot.bus.queue import MessageBus


# ── 凭据解析：环境变量优先，否则读取 config.json ──

def _resolve_credentials() -> tuple[str | None, str | None, str | None]:
    """返回 (api_key, base_url, model)，找不到则返回 None。"""
    api_key = os.environ.get("OPENAI_API_KEY")
    base_url = os.environ.get("OPENAI_BASE_URL")
    model = os.environ.get("OPENAI_MODEL")

    if api_key and base_url:
        return api_key, base_url, model or "gpt-4o-mini"

    # 回退：从 ~/.ultrabot/config.json 读取
    try:
        from ultrabot.config import load_config
        cfg = load_config()
        provider_name = cfg.agents.defaults.provider
        prov = cfg.providers.all_providers().get(provider_name)
        if prov and prov.api_key and prov.enabled:
            return (
                api_key or prov.api_key,
                base_url or prov.api_base,
                model or cfg.agents.defaults.model,
            )
    except Exception:
        pass

    return api_key, base_url, model


_api_key, _base_url, _model = _resolve_credentials()

_skip = pytest.mark.skipif(
    not _api_key or not _base_url,
    reason="No LLM credentials: set OPENAI_API_KEY + OPENAI_BASE_URL, "
           "or configure ~/.ultrabot/config.json",
)


def _make_agent() -> Agent:
    """构建一个连接到真实 LLM 的 Agent。"""
    client = OpenAI(api_key=_api_key, base_url=_base_url)
    return Agent(client=client, model=_model)


@_skip
def test_bus_with_real_agent():
    """完整链路：用户消息 → 总线 → Agent → LLM → 出站回复。"""
    agent = _make_agent()

    async def _run():
        bus = MessageBus()
        delivered: list[OutboundMessage] = []

        async def agent_handler(msg: InboundMessage) -> OutboundMessage:
            reply = await agent.run(msg.content, session_key=msg.session_key)
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=reply,
            )

        async def channel_sender(msg: OutboundMessage):
            delivered.append(msg)

        bus.set_inbound_handler(agent_handler)
        bus.subscribe(channel_sender)

        await bus.publish(InboundMessage(
            channel="telegram", sender_id="u1",
            chat_id="c1", content="请用一句话介绍你自己",
        ))

        task = asyncio.create_task(bus.dispatch_inbound())
        await asyncio.sleep(30)
        bus.shutdown()
        await task

        assert len(delivered) == 1
        assert len(delivered[0].content) > 0
        print(f"\n[Agent replied] {delivered[0].content}")

    asyncio.run(_run())


@_skip
def test_bus_multi_turn_conversation():
    """多轮对话：验证总线能串行处理多条消息并保持会话。"""
    agent = _make_agent()

    async def _run():
        bus = MessageBus()
        delivered: list[OutboundMessage] = []

        async def agent_handler(msg: InboundMessage) -> OutboundMessage:
            reply = await agent.run(msg.content, session_key=msg.session_key)
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=reply,
            )

        async def channel_sender(msg: OutboundMessage):
            delivered.append(msg)

        bus.set_inbound_handler(agent_handler)
        bus.subscribe(channel_sender)

        # 第一轮
        await bus.publish(InboundMessage(
            channel="telegram", sender_id="u1",
            chat_id="c1", content="我的名字叫小明，请记住",
        ))

        task = asyncio.create_task(bus.dispatch_inbound())
        await asyncio.sleep(30)

        # 第二轮 — 测试 Agent 是否记住了上下文
        await bus.publish(InboundMessage(
            channel="telegram", sender_id="u1",
            chat_id="c1", content="我叫什么名字？",
        ))
        await asyncio.sleep(30)

        bus.shutdown()
        await task

        assert len(delivered) == 2
        print(f"\n[Round 1] {delivered[0].content}")
        print(f"[Round 2] {delivered[1].content}")
        assert "小明" in delivered[1].content

    asyncio.run(_run())
```

如果已配置 `~/.ultrabot/config.json`，直接运行即可：

```bash
python -m pytest tests/test_bus_real_agent.py -v -s
```

也可以用环境变量覆盖配置文件中的设置：

```bash
OPENAI_API_KEY=你的密钥 \
OPENAI_BASE_URL=https://ark.cn-beijing.volces.com/api/v3 \
OPENAI_MODEL=ep-xxx \
  python -m pytest tests/test_bus_real_agent.py -v -s
```

### 检查点

```bash
python -m pytest tests/ -v
```

预期结果：如果 `~/.ultrabot/config.json` 已配置提供者，全部 9 个测试通过；
否则 7 个通过、2 个跳过（真实 Agent 测试在找不到 LLM 凭据时自动跳过）。

```
tests/test_bus.py::test_priority_ordering              PASSED
tests/test_bus.py::test_session_key_derivation          PASSED
tests/test_bus.py::test_bus_dispatch_and_dead_letter     PASSED
tests/test_bus.py::test_bus_outbound_fanout              PASSED
tests/test_bus_integration.py::test_end_to_end_message_flow   PASSED
tests/test_bus_integration.py::test_multi_channel_routing     PASSED
tests/test_bus_integration.py::test_priority_dispatch_order   PASSED
tests/test_bus_real_agent.py::test_bus_with_real_agent         PASSED (or SKIPPED)
tests/test_bus_real_agent.py::test_bus_multi_turn_conversation PASSED (or SKIPPED)
```

### 本课成果

一个事件驱动的 `MessageBus`，具有用于入站消息的 `asyncio.PriorityQueue`
（优先级越高 = 越先处理）、带死信语义的重试循环，
以及将出站消息扇出到多个订阅者的分发机制。集成测试验证了
完整的入站→处理→出站链路、多通道路由和优先级调度。

---

## 本课使用的 Python 知识

### `from __future__ import annotations`（延迟注解评估）

这是 Python 3.7 引入的特性，让类型注解在运行时不被立即求值，而是保存为字符串。这样可以在类型注解中引用尚未定义的类，避免循环引用问题。

```python
from __future__ import annotations

class Node:
    # 可以引用自身类型，不会报错
    def connect(self, other: Node) -> None:
        pass
```

**为什么在本课中使用：** `InboundMessage` 的 `__lt__` 方法参数类型标注为 `InboundMessage`（引用自身类），如果没有这行导入，Python 在定义类时会找不到自身类名。

### `@dataclass` 装饰器（数据类）

`@dataclass` 是 Python 3.7 引入的装饰器，可以自动为类生成 `__init__`、`__repr__`、`__eq__` 等方法，极大减少样板代码。只需声明字段和类型，Python 就会自动帮你写好构造函数。

```python
from dataclasses import dataclass

@dataclass
class Point:
    x: float
    y: float
    # 自动生成 __init__(self, x, y)、__repr__() 等
```

**为什么在本课中使用：** `InboundMessage` 和 `OutboundMessage` 都是纯粹的数据容器（携带通道名、发送者、内容等字段），使用 `@dataclass` 可以用最少的代码定义它们，而不需要手写冗长的 `__init__` 方法。

### `field(default_factory=...)` 数据类字段工厂

在 `@dataclass` 中，如果一个字段的默认值是可变对象（如列表、字典），不能直接写 `media: list = []`，否则所有实例会共享同一个列表。`field(default_factory=list)` 确保每次创建实例时都生成一个全新的空列表。

```python
from dataclasses import dataclass, field

@dataclass
class Bag:
    items: list[str] = field(default_factory=list)  # 每个 Bag 有自己的列表
```

**为什么在本课中使用：** `InboundMessage` 中的 `media`、`metadata` 字段以及 `timestamp` 字段都使用了 `default_factory`，确保每条消息都有独立的列表/字典/时间戳，避免实例之间的数据污染。

### `lambda` 匿名函数

`lambda` 用于创建简短的一次性函数，语法为 `lambda 参数: 表达式`。它适合用在只需要一个简单表达式的地方。

```python
square = lambda x: x * x
print(square(5))  # 25
```

**为什么在本课中使用：** `timestamp` 字段的 `default_factory=lambda: datetime.now(timezone.utc)` 使用 lambda 包装了一个带参数的函数调用，因为 `default_factory` 要求传入一个无参可调用对象。

### `@property` 属性装饰器

`@property` 让你可以像访问普通属性一样调用一个方法，无需加括号。它常用于将计算逻辑包装成"看起来像属性"的接口。

```python
class Circle:
    def __init__(self, radius):
        self.radius = radius

    @property
    def area(self):
        return 3.14 * self.radius ** 2

c = Circle(5)
print(c.area)  # 78.5，不需要写 c.area()
```

**为什么在本课中使用：** `session_key` 被定义为属性，外部代码使用 `msg.session_key` 就能获取会话键，内部则自动根据是否有覆盖值来决定返回什么，封装了推导逻辑。

### `__lt__` 魔术方法（运算符重载）

`__lt__` 是 Python 的"小于"比较魔术方法。当你写 `a < b` 时，Python 实际调用的是 `a.__lt__(b)`。通过自定义它，可以改变对象的排序行为。

```python
class Task:
    def __init__(self, priority):
        self.priority = priority

    def __lt__(self, other):
        return self.priority > other.priority  # 反转！高优先级排前面

tasks = [Task(1), Task(10), Task(5)]
tasks.sort()  # Task(10) 排最前面
```

**为什么在本课中使用：** `asyncio.PriorityQueue` 内部使用最小堆，"最小"的元素先出队。通过反转 `__lt__`，让 priority 值大的消息被认为"更小"，从而优先出队。

### `NotImplemented` 特殊返回值

`NotImplemented` 是 Python 内置的特殊单例值（注意不是 `NotImplementedError` 异常）。在比较魔术方法中返回它，意思是"我不知道怎么跟这个类型比较，请让对方试试"。

```python
def __lt__(self, other):
    if not isinstance(other, MyClass):
        return NotImplemented  # 告诉 Python：我处理不了这个比较
    return self.value < other.value
```

**为什么在本课中使用：** `InboundMessage.__lt__` 在 `other` 不是同类型时返回 `NotImplemented`，确保与其他类型比较时不会产生意外结果。

### `asyncio.PriorityQueue`（异步优先级队列）

这是 `asyncio` 提供的线程安全优先级队列，内部基于堆实现。`put()` 放入元素，`get()` 取出最"小"的元素。两者都是异步操作，没有数据时 `get()` 会自动等待。

```python
import asyncio

queue = asyncio.PriorityQueue()
await queue.put((2, "low priority"))
await queue.put((1, "high priority"))
item = await queue.get()  # 拿到 (1, "high priority")
```

**为什么在本课中使用：** 消息总线需要按优先级处理入站消息，优先级队列天然支持这种需求，配合 `InboundMessage.__lt__` 的反转比较，实现了"高优先级先处理"。

### `asyncio.Event`（异步事件信号）

`asyncio.Event` 是一个简单的信号机制：初始状态为"未设置"，调用 `set()` 后变为"已设置"。配合 `is_set()` 检查或 `wait()` 等待，常用于协调协程之间的通信。

```python
import asyncio

shutdown = asyncio.Event()

async def worker():
    while not shutdown.is_set():
        print("Working...")
        await asyncio.sleep(1)

# 某处调用 shutdown.set() 通知 worker 停止
```

**为什么在本课中使用：** `_shutdown_event` 用于通知 `dispatch_inbound` 循环优雅退出。当调用 `shutdown()` 时设置事件，分发循环检测到后自动停止。

### `asyncio.wait_for` 和 `asyncio.TimeoutError`（超时控制）

`asyncio.wait_for` 给一个异步操作加上超时限制。如果在指定时间内没有完成，会抛出 `asyncio.TimeoutError`。

```python
try:
    result = await asyncio.wait_for(some_coroutine(), timeout=5.0)
except asyncio.TimeoutError:
    print("操作超时了！")
```

**为什么在本课中使用：** 分发循环使用 `wait_for` 给 `queue.get()` 加了 1 秒超时。如果队列中没有消息，超时后循环会回到顶部检查 `_shutdown_event`，从而实现"不阻塞在空队列上，又能及时响应关闭信号"。

### `asyncio.create_task`（创建异步任务）

`asyncio.create_task()` 把一个协程包装成一个 Task，让它在事件循环中并发运行。创建后无需立即 `await`，Task 会在后台执行。

```python
async def background_job():
    await asyncio.sleep(5)
    print("Done!")

task = asyncio.create_task(background_job())  # 立即返回，后台执行
```

**为什么在本课中使用：** 测试代码中用 `create_task(bus.dispatch_inbound())` 启动分发循环，使其在后台运行，测试可以继续操作（如等待一段时间后调用 `shutdown()`）。

### 类型别名（Type Alias）

类型别名是给复杂的类型签名起一个简短的名字，提高代码可读性。

```python
from collections.abc import Callable, Coroutine
from typing import Any

# 原始类型太长：Callable[[InboundMessage], Coroutine[Any, Any, OutboundMessage | None]]
# 起个别名：
InboundHandler = Callable[[InboundMessage], Coroutine[Any, Any, OutboundMessage | None]]
```

**为什么在本课中使用：** 消息总线需要注册回调处理器，其函数签名很长。定义 `InboundHandler` 和 `OutboundSubscriber` 类型别名后，代码中多次引用这些类型时更加简洁易读。

### `Callable` 和 `Coroutine`（可调用对象与协程类型）

`Callable[[参数类型], 返回类型]` 表示一个可调用对象（函数）。`Coroutine[YieldType, SendType, ReturnType]` 表示一个协程对象。组合使用可以精确描述"接受某些参数并返回协程"的异步函数签名。

```python
from collections.abc import Callable, Coroutine
from typing import Any

# 表示：接受一个 str 参数，返回 Coroutine（即 async 函数）
AsyncStringHandler = Callable[[str], Coroutine[Any, Any, None]]
```

**为什么在本课中使用：** 消息总线的处理器和订阅者都是异步函数，使用 `Callable` + `Coroutine` 组合类型来精确标注它们的签名，方便 IDE 提供代码补全和类型检查。

### `str | None` 联合类型（PEP 604）

Python 3.10+ 允许用 `X | Y` 语法代替 `Union[X, Y]`，表示一个值可以是 X 类型或 Y 类型。`str | None` 等同于 `Optional[str]`。

```python
def find_user(name: str) -> str | None:
    if name == "admin":
        return "found"
    return None
```

**为什么在本课中使用：** `session_key_override: str | None = None` 表示该字段可以是字符串或 None，`reply_to: str | None = None` 同理。配合 `from __future__ import annotations`，即使在 Python 3.9 中也能使用此语法。

### `f-string` 格式化字符串

f-string 是 Python 3.6 引入的字符串格式化语法，在字符串前加 `f`，花括号内可以直接嵌入 Python 表达式。

```python
name = "Alice"
age = 30
print(f"Hello, {name}! You are {age} years old.")
```

**为什么在本课中使用：** `session_key` 属性中使用 `f"{self.channel}:{self.chat_id}"` 来拼接会话键，比字符串拼接或 `.format()` 更简洁直观。

### `__all__` 模块导出控制

`__all__` 是一个列表，定义了当其他模块使用 `from package import *` 时，哪些名字会被导出。它也是一种文档约定，声明模块的公共 API。

```python
# mypackage/__init__.py
from .module_a import ClassA
from .module_b import ClassB

__all__ = ["ClassA", "ClassB"]
```

**为什么在本课中使用：** `ultrabot/bus/__init__.py` 中用 `__all__` 明确声明包的公共接口为 `InboundMessage`、`OutboundMessage` 和 `MessageBus`，让使用者清楚知道该用哪些类。

### `asyncio.run`（运行异步代码的入口）

`asyncio.run()` 是启动异步程序的标准入口，它创建一个新的事件循环，运行传入的协程直到完成，然后关闭事件循环。

```python
import asyncio

async def main():
    print("Hello from async!")

asyncio.run(main())
```

**为什么在本课中使用：** 测试函数是普通的同步函数（pytest 默认），但需要测试异步代码。用 `asyncio.run(_run())` 在同步测试中运行异步逻辑。

### `try / except` 异常处理与重试模式

`try/except` 用来捕获和处理异常。配合循环使用可以实现重试逻辑：多次尝试，每次失败后记录错误，全部失败后执行兜底操作。

```python
for attempt in range(1, 4):
    try:
        do_something()
        break  # 成功就退出循环
    except Exception as e:
        print(f"Attempt {attempt} failed: {e}")
```

**为什么在本课中使用：** `_process_with_retries` 方法对每条消息最多重试 `max_retries` 次，全部失败后将消息放入死信队列，确保单条消息的异常不会导致整个系统崩溃。
