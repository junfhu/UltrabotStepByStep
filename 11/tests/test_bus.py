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
