# 课程 11：消息总线 + 事件

**目标：** 通过基于优先级的异步消息总线，将消息生产者（通道）与消费者（智能体）解耦。

**你将学到：**
- 设计 `InboundMessage` 和 `OutboundMessage` 数据类
- 使用自定义排序的 `asyncio.PriorityQueue`
- 出站消息的扇出（fan-out）模式
- 用于重试耗尽的消息的死信队列
- 使用 `asyncio.Event` 实现优雅关闭

**新建文件：**
- `ultrabot/bus/__init__.py` — 公共重导出
- `ultrabot/bus/events.py` — `InboundMessage` 和 `OutboundMessage` 数据类
- `ultrabot/bus/queue.py` — 带优先级队列的 `MessageBus`

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

### 检查点

```bash
python -m pytest tests/test_bus.py -v
```

预期结果：全部 4 个测试通过。消息总线现在已准备好放置在通道和
智能体之间。

### 本课成果

一个事件驱动的 `MessageBus`，具有用于入站消息的 `asyncio.PriorityQueue`
（优先级越高 = 越先处理）、带死信语义的重试循环，
以及将出站消息扇出到多个订阅者的分发机制。

---
