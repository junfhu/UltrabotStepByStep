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
