"""独立演示脚本：验证 DiscordChannel 能收发消息。"""

import asyncio
import os

from ultrabot.bus.events import InboundMessage
from ultrabot.bus.queue import MessageBus
from ultrabot.channels.discord_channel import DiscordChannel


async def main():
    bus = MessageBus()

    async def on_inbound(msg: InboundMessage):
        print(f"收到消息 [{msg.sender_id}]: {msg.content}")

    bus.set_inbound_handler(on_inbound)

    token = os.environ["DISCORD_BOT_TOKEN"]
    channel = DiscordChannel({"token": token}, bus)

    await channel.start()
    print("Discord 通道已启动，在服务器频道中发消息试试！")
    print("按 Ctrl+C 退出")

    try:
        await bus.dispatch_inbound()
    except KeyboardInterrupt:
        pass
    finally:
        await channel.stop()


asyncio.run(main())
