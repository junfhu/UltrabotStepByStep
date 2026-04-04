# ultrabot/channels/qq.py
"""使用 botpy SDK 的 QQ Bot 通道。"""

def _make_bot_class(channel: "QQChannel") -> "type[botpy.Client]":
    """创建绑定到给定通道的 botpy Client 子类。"""
    intents = botpy.Intents(public_messages=True, direct_message=True)

    class _Bot(botpy.Client):
        def __init__(self):
            super().__init__(intents=intents, ext_handlers=False)

        async def on_ready(self):
            logger.info("QQ bot ready: {}", self.robot.name)

        async def on_c2c_message_create(self, message):
            await channel._on_message(message, is_group=False)

        async def on_group_at_message_create(self, message):
            await channel._on_message(message, is_group=True)

    return _Bot


class QQChannel(BaseChannel):
    """QQ Bot 通道 — C2C 和群消息。"""

    @property
    def name(self) -> str:
        return "qq"

    def __init__(self, config: dict, bus: "MessageBus") -> None:
        super().__init__(config, bus)
        self._app_id = config.get("appId", "")
        self._secret = config.get("secret", "")
        self._msg_format = config.get("msgFormat", "plain")  # 或 "markdown"
        self._chat_type_cache: dict[str, str] = {}

    async def start(self) -> None:
        self._client = _make_bot_class(self)()
        await self._client.start(
            appid=self._app_id, secret=self._secret
        )

    async def send(self, msg: "OutboundMessage") -> None:
        """根据配置发送文本（纯文本或 markdown）。"""
        chat_type = self._chat_type_cache.get(msg.chat_id, "c2c")
        is_group = chat_type == "group"

        payload = {
            "msg_type": 2 if self._msg_format == "markdown" else 0,
            "content": msg.content if self._msg_format == "plain" else None,
            "markdown": {"content": msg.content}
                if self._msg_format == "markdown" else None,
        }

        if is_group:
            await self._client.api.post_group_message(
                group_openid=msg.chat_id, **payload
            )
        else:
            await self._client.api.post_c2c_message(
                openid=msg.chat_id, **payload
            )
