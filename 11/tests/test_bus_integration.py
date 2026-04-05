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
