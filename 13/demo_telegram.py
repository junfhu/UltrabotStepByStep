"""独立演示脚本：验证 TelegramChannel 能收发消息。"""

import asyncio
import os

from ultrabot.bus.events import InboundMessage
from ultrabot.bus.queue import MessageBus
from ultrabot.channels.telegram import TelegramChannel


async def main():
    bus = MessageBus()

    # 收到消息时打印到终端
    async def on_inbound(msg: InboundMessage):
        print(f"收到消息 [{msg.sender_id}]: {msg.content}")

    bus.set_inbound_handler(on_inbound)

    token = os.environ["TELEGRAM_BOT_TOKEN"]
    channel = TelegramChannel({"token": token}, bus)

    await channel.start()
    print("Telegram 通道已启动，在 Telegram 上给你的机器人发消息试试！")
    print("按 Ctrl+C 退出")

    try:
        await bus.dispatch_inbound()
    except KeyboardInterrupt:
        pass
    finally:
        await channel.stop()


asyncio.run(main())
