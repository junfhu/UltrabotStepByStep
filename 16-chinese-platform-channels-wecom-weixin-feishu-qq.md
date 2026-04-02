# 课程 16：中国平台通道（企业微信、微信、飞书、QQ）

**目标：** 添加对四个主要中国消息平台的支持，每个平台都有独特的连接模式：WebSocket、HTTP 长轮询、SDK 驱动和 Bot API。

**你将学到：**
- 企业微信（WeCom）：WebSocket 长连接、事件驱动回调
- 微信（Weixin）个人号：HTTP 长轮询、二维码登录、AES 加密
- 飞书（Lark）：`lark-oapi` SDK、在专用线程中运行 WebSocket
- QQ：`botpy` SDK、C2C 和群消息、富媒体上传
- 通用模式：消息去重、允许列表、媒体下载、可选导入

**新建文件：**
- `ultrabot/channels/wecom.py` — `WecomChannel`
- `ultrabot/channels/weixin.py` — `WeixinChannel`
- `ultrabot/channels/feishu.py` — `FeishuChannel`
- `ultrabot/channels/qq.py` — `QQChannel`

### 通用模式

在深入每个通道之前，请注意四个通道共享的四种模式：

1. **带可用性标志的可选导入：**
   ```python
   _WECOM_AVAILABLE = importlib.util.find_spec("wecom_aibot_sdk") is not None

   def _require_wecom() -> None:
       if not _WECOM_AVAILABLE:
           raise ImportError("wecom-aibot-sdk is required...")
   ```

2. **消息去重**，使用 `OrderedDict` 作为有界集合：
   ```python
   if msg_id in self._processed_ids:
       return
   self._processed_ids[msg_id] = None
   while len(self._processed_ids) > 1000:
       self._processed_ids.popitem(last=False)   # 淘汰最旧的
   ```

3. **逐发送者的允许列表**（四个通道的模式完全相同）。

4. **所有通道都向同一个 `MessageBus` 发布 `InboundMessage`** — 
   智能体不需要知道也不关心消息来自哪个平台。

### 步骤 1：企业微信（WeCom）— WebSocket 长连接

企业微信使用 WebSocket SDK（`wecom-aibot-sdk`）— 不需要公网 IP。
机器人通过 Bot ID 和密钥进行认证，然后通过回调接收事件。

```python
# ultrabot/channels/wecom.py（关键部分）
"""使用 wecom_aibot_sdk WebSocket 长连接的企业微信通道。"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

from loguru import logger
from ultrabot.channels.base import BaseChannel

if TYPE_CHECKING:
    from ultrabot.bus.events import OutboundMessage
    from ultrabot.bus.queue import MessageBus

import importlib.util
_WECOM_AVAILABLE = importlib.util.find_spec("wecom_aibot_sdk") is not None


class WecomChannel(BaseChannel):
    """使用 WebSocket 长连接的企业微信通道。"""

    @property
    def name(self) -> str:
        return "wecom"

    def __init__(self, config: dict, bus: "MessageBus") -> None:
        super().__init__(config, bus)
        self._bot_id: str = config.get("botId", "")
        self._secret: str = config.get("secret", "")
        self._allow_from: list[str] = config.get("allowFrom", [])
        self._welcome_message: str = config.get("welcomeMessage", "")
        self._client: Any = None
        self._processed_ids: OrderedDict[str, None] = OrderedDict()
        self._chat_frames: dict[str, Any] = {}   # 用于回复路由

    async def start(self) -> None:
        from wecom_aibot_sdk import WSClient, generate_req_id

        self._generate_req_id = generate_req_id
        self._client = WSClient({
            "bot_id": self._bot_id,
            "secret": self._secret,
            "reconnect_interval": 1000,
            "max_reconnect_attempts": -1,
            "heartbeat_interval": 30000,
        })

        # 注册事件处理器。
        self._client.on("message.text", self._on_text_message)
        self._client.on("event.enter_chat", self._on_enter_chat)
        # ... 图片、语音、文件、混合消息处理器 ...

        await self._client.connect_async()

    async def send(self, msg: "OutboundMessage") -> None:
        """使用流式回复 API 进行回复。"""
        frame = self._chat_frames.get(msg.chat_id)
        if not frame:
            logger.warning("No frame for chat {}", msg.chat_id)
            return
        stream_id = self._generate_req_id("stream")
        await self._client.reply_stream(
            frame, stream_id, msg.content.strip(), finish=True
        )
```

**关键洞察：** 企业微信为每个聊天存储传入的 `frame` 对象，以便
出站回复可以引用原始对话上下文。

### 步骤 2：微信（个人号）— HTTP 长轮询 + AES 加密

微信通过 HTTP 长轮询连接到 `ilinkai.weixin.qq.com`。
认证通过二维码登录流程完成，媒体文件使用
AES-128-ECB 加密。

```python
# ultrabot/channels/weixin.py（关键部分）
"""使用 HTTP 长轮询的个人微信通道。"""

class WeixinChannel(BaseChannel):
    """使用 HTTP 长轮询连接 ilinkai.weixin.qq.com 的个人微信。"""

    @property
    def name(self) -> str:
        return "weixin"

    def __init__(self, config: dict, bus: "MessageBus") -> None:
        super().__init__(config, bus)
        self._base_url = config.get("baseUrl",
            "https://ilinkai.weixin.qq.com")
        self._configured_token = config.get("token", "")
        self._state_dir = Path.home() / ".ultrabot" / "weixin"
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(45, connect=30),
            follow_redirects=True,
        )

        # 尝试已保存的 token，然后二维码登录。
        if not self._configured_token and not self._load_state():
            if not await self._qr_login():
                logger.error("WeChat login failed")
                return

        # 主轮询循环。
        while self._running:
            try:
                await self._poll_once()
            except httpx.TimeoutException:
                continue
            except Exception as exc:
                logger.error("Poll error: {}", exc)
                await asyncio.sleep(2)
```

**AES 加密**用于媒体文件。该通道同时支持
`pycryptodome` 和 `cryptography` 作为后端：

```python
def _decrypt_aes_ecb(data: bytes, aes_key_b64: str) -> bytes:
    """解密 AES-128-ECB 媒体数据。"""
    key = _parse_aes_key(aes_key_b64)
    try:
        from Crypto.Cipher import AES
        return AES.new(key, AES.MODE_ECB).decrypt(data)
    except ImportError:
        pass
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    decryptor = cipher.decryptor()
    return decryptor.update(data) + decryptor.finalize()
```

### 步骤 3：飞书（Lark）— 在专用线程中运行 SDK WebSocket

飞书使用 `lark-oapi` SDK。该 SDK 的 WebSocket 客户端运行自己的
事件循环，这会与 ultrabot 的主循环冲突。解决方案：在专用线程中运行。

```python
# ultrabot/channels/feishu.py（关键部分）
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
```

**关键洞察：** `run_coroutine_threadsafe` 将 SDK 的同步回调
桥接到主 asyncio 循环。飞书 SDK 在后台线程中管理自己的事件循环。

### 步骤 4：QQ Bot — 使用 WebSocket 的 botpy SDK

QQ 使用 `botpy` SDK。该 SDK 提供一个 `Client` 基类，你通过
子类化来处理事件。我们使用工厂函数创建
与通道实例绑定的子类。

```python
# ultrabot/channels/qq.py（关键部分）
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
```

### 平台对比

| 特性 | 企业微信 | 微信 | 飞书 | QQ |
|------|---------|------|------|-----|
| 连接方式 | WebSocket | HTTP 长轮询 | WebSocket（线程） | WebSocket |
| 认证方式 | Bot ID + Secret | 二维码登录 | App ID + Secret | App ID + Secret |
| 加密 | SDK 管理 | AES-128-ECB | SDK 管理 | 无 |
| 群组支持 | 是 | 否（个人号） | 是（@提及） | 是（@提及） |
| 媒体类型 | 图片/语音/文件 | 图片/语音/视频/文件 | 图片/音频/文件 | 图片/文件 |
| SDK | `wecom-aibot-sdk` | `httpx`（原始） | `lark-oapi` | `qq-botpy` |

### 测试

```python
# tests/test_chinese_channels.py
"""验证中国平台通道类可以导入并具有正确的接口。"""

import importlib


def test_wecom_channel_importable():
    spec = importlib.util.find_spec("ultrabot.channels.wecom")
    assert spec is not None
    mod = importlib.import_module("ultrabot.channels.wecom")
    assert hasattr(mod, "WecomChannel")


def test_weixin_channel_importable():
    spec = importlib.util.find_spec("ultrabot.channels.weixin")
    assert spec is not None
    mod = importlib.import_module("ultrabot.channels.weixin")
    assert hasattr(mod, "WeixinChannel")


def test_feishu_channel_importable():
    spec = importlib.util.find_spec("ultrabot.channels.feishu")
    assert spec is not None
    mod = importlib.import_module("ultrabot.channels.feishu")
    assert hasattr(mod, "FeishuChannel")


def test_qq_channel_importable():
    spec = importlib.util.find_spec("ultrabot.channels.qq")
    assert spec is not None
    mod = importlib.import_module("ultrabot.channels.qq")
    assert hasattr(mod, "QQChannel")


def test_all_channels_extend_base():
    from ultrabot.channels.base import BaseChannel
    from ultrabot.channels.weixin import WeixinChannel

    assert issubclass(WeixinChannel, BaseChannel)


def test_weixin_message_chunking():
    """验证微信消息分割辅助函数。"""
    from ultrabot.channels.weixin import _split_message

    chunks = _split_message("A" * 10000, 4000)
    assert len(chunks) == 3
    assert all(len(c) <= 4000 for c in chunks)
    assert "".join(chunks) == "A" * 10000


def test_weixin_aes_key_parsing():
    """验证 AES 密钥解析可以处理 16 字节的原始密钥。"""
    import base64
    from ultrabot.channels.weixin import _parse_aes_key

    raw_key = b"0123456789abcdef"            # 16 字节
    b64_key = base64.b64encode(raw_key).decode()
    parsed = _parse_aes_key(b64_key)
    assert parsed == raw_key
```

### 检查点

```bash
python -m pytest tests/test_chinese_channels.py -v
```

预期结果：全部 7 个测试通过。通道类可以正确加载，其
实用函数正常工作 — 即使没有安装平台特定的 SDK
（微信仅使用核心依赖中的 `httpx`）。

要进行通道实际测试，将凭据添加到 `ultrabot.yaml`：

```yaml
channels:
  feishu:
    enabled: true
    appId: "cli_xxxxx"
    appSecret: "xxxxx"
```

然后运行 `python -m ultrabot gateway` 并在飞书上发送消息。

### 本课成果

四个中国消息平台通道 — 企业微信（WebSocket SDK）、微信
（HTTP 长轮询 + AES 加密）、飞书（在专用线程中运行 SDK WebSocket）
和 QQ（botpy SDK）— 全部实现相同的 `BaseChannel` 接口。
智能体和消息总线完全不感知底层平台。
# Ultrabot 开发者指南 — 第 3 部分：课程 17-23

> **前置条件：** 课程 1-16 全部完成（LLM 聊天、流式输出、工具、工具集、
> 配置、提供者、Anthropic、CLI、会话、熔断器、消息总线、
> 安全、Telegram、Discord/Slack、网关、中国平台）。

---
