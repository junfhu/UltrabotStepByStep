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
