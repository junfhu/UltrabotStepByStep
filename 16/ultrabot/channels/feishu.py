# ultrabot/channels/feishu.py
"""使用 lark-oapi SDK 和 WebSocket 的飞书/Lark 通道。"""

class FeishuChannel(BaseChannel):
    """飞书通道 — WebSocket，无需公网 IP。"""

    @property
    def name(self) -> str:
        return "feishu"

    def __init__(self, config: dict, bus: "MessageBus") -> None:
        super().__init__(config, bus)
        self._app_id = config.get("appId", "")
        self._app_secret = config.get("appSecret", "")
        self._encrypt_key = config.get("encryptKey", "")
        self._react_emoji = config.get("reactEmoji", "THUMBSUP")
        self._group_policy = config.get("groupPolicy", "mention")
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        import lark_oapi as lark

        self._loop = asyncio.get_running_loop()

        # 用于发送消息的 Lark 客户端。
        self._client = (lark.Client.builder()
            .app_id(self._app_id)
            .app_secret(self._app_secret)
            .build())

        # 事件分发器。
        event_handler = (lark.EventDispatcherHandler.builder(
                self._encrypt_key, "")
            .register_p2_im_message_receive_v1(self._on_message_sync)
            .build())

        self._ws_client = lark.ws.Client(
            self._app_id, self._app_secret,
            event_handler=event_handler,
        )

        # 在专用线程中运行 WebSocket — 避免事件循环冲突。
        def _run_ws():
            import lark_oapi.ws.client as _lark_ws_client
            ws_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(ws_loop)
            _lark_ws_client.loop = ws_loop
            try:
                while self._running:
                    try:
                        self._ws_client.start()
                    except Exception:
                        if self._running:
                            time.sleep(5)
            finally:
                ws_loop.close()

        import threading
        self._ws_thread = threading.Thread(target=_run_ws, daemon=True)
        self._ws_thread.start()

    def _on_message_sync(self, data: Any) -> None:
        """WS 线程中的同步回调 → 在主循环上调度异步工作。"""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._on_message(data), self._loop
            )
