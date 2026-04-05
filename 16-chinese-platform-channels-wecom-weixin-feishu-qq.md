# Ultrabot：30 课程开发指南
**从零开始构建一个生产级 AI 助手框架。**
本指南将带你从"向 LLM 问好"一步步走到一个完整的多提供者、多通道 AI 智能体，具备工具调用、记忆、安全防护和 Web 界面。每节课程都建立在上一节课的基础之上。每节课都包含可运行的代码和测试。  
本教程的主要思路来自于
- Nanobot (https://github.com/HKUDS/nanobot)
- Learn-Claude-Code (https://github.com/shareAI-lab/learn-claude-code/)

本课程设计由AI辅助下完成，因为课程自身也在不停修正，请参考 https://github.com/junfhu/UltrabotStepByStep，如果您觉得对您有帮助，请帮助点亮一颗星。  
本课程中使用的大模型提供商是火山引擎Code Plan，如果正好你也需要，可以使用我的邀请码获取9折优惠 https://volcengine.com/L/_01BJCkKdMc/  邀请码：HHCDB4J4）  



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

要进行通道实际测试，将凭据添加到 `~/.ultrabot/config.json`：

```json
{
  "channels": {
    "feishu": {
      "enabled": true,
      "appId": "${FEISHU_APP_ID}",
      "appSecret": "${FEISHU_APP_SECRET}",
      "encryptKey": ""
    }
  }
}
```

然后运行 `python -m ultrabot.gateway` 并在飞书上发送消息。

### 步骤 5：各平台机器人创建与连接

#### 5.1 企业微信（WeCom）

**创建机器人：**

1. 登录 [企业微信管理后台](https://work.weixin.qq.com/)。
2. 进入 **应用管理** → **创建应用**，选择 **机器人** 类型。
3. 获取 **Bot ID** 和 **Secret**。

**配置：**

```json
{
  "channels": {
    "wecom": {
      "enabled": true,
      "botId": "${WECOM_BOT_ID}",
      "secret": "${WECOM_SECRET}",
      "welcomeMessage": "你好，我是 Ultrabot！",
      "allowFrom": []
    }
  }
}
```

```bash
export WECOM_BOT_ID="你的BotID"
export WECOM_SECRET="你的Secret"
pip install wecom-aibot-sdk
```

> **排查提示：** 企业微信使用 WebSocket 长连接，不需要公网 IP。如果连接断开，SDK 会自动重连。

#### 5.2 微信个人号（Weixin）

**登录方式：** 微信个人号通过二维码扫码登录，不需要在开发者平台创建应用。

**配置：**

```json
{
  "channels": {
    "weixin": {
      "enabled": true,
      "baseUrl": "https://ilinkai.weixin.qq.com",
      "token": "",
      "allowFrom": []
    }
  }
}
```

首次启动时，终端会显示二维码，用手机微信扫码登录。登录状态会保存在 `~/.ultrabot/weixin/` 目录。

```bash
pip install httpx pycryptodome
```

> **排查提示：**
> - 二维码不显示？确认终端支持 Unicode 字符。
> - 频繁掉线？微信对自动化登录有限制，建议使用专用微信号。
> - 媒体文件解密失败？确认 `pycryptodome` 或 `cryptography` 已安装。

#### 5.3 飞书（Feishu / Lark）

**创建机器人：**

1. 打开 [飞书开放平台](https://open.feishu.cn/)，点击 **创建企业自建应用**。
2. 输入应用名称和描述，点击 **确定创建**。
3. 在 **凭证与基础信息** 页面获取 **App ID** 和 **App Secret**。
4. 进入 **事件与回调** → **事件配置** → 选择 **使用长连接接收事件**。
5. 在 **事件订阅** 中添加 `im.message.receive_v1`（接收消息事件）。
6. 进入 **权限管理**，添加以下权限并申请开通：
   - `im:message` — 获取与发送消息
   - `im:message:send_as_bot` — 以机器人身份发送消息
   - `im:chat` — 获取群信息
7. 在 **版本管理与发布** 中创建版本并发布。

**配置：**

```json
{
  "channels": {
    "feishu": {
      "enabled": true,
      "appId": "${FEISHU_APP_ID}",
      "appSecret": "${FEISHU_APP_SECRET}",
      "encryptKey": "",
      "reactEmoji": "THUMBSUP",
      "groupPolicy": "mention"
    }
  }
}
```

配置说明：
- `encryptKey` — 事件加密密钥（可为空，在开放平台的事件配置中设置）
- `reactEmoji` — 收到消息时自动添加的表情回应
- `groupPolicy` — 群聊策略：`"mention"` 只响应 @机器人 的消息，`"all"` 响应所有消息

```bash
export FEISHU_APP_ID="cli_xxxx"
export FEISHU_APP_SECRET="xxxx"
pip install lark-oapi
```

> **排查提示：**
> - 飞书 SDK 在专用线程中运行，与主 asyncio 循环不冲突。
> - 机器人不回复群消息？确认 `groupPolicy` 设置正确，且在群中 @了机器人。
> - 提示权限不足？检查是否已在开放平台申请并开通了所需权限。

#### 5.4 QQ Bot

**创建机器人：**

1. 打开 [QQ 开放平台](https://q.qq.com/)，注册开发者账号。
2. 点击 **创建机器人**，填写信息后提交审核。
3. 审核通过后，在 **开发设置** 中获取 **App ID** 和 **Secret**。
4. 在 **功能配置** 中开启需要的消息类型（C2C 私聊、群聊等）。

**配置：**

```json
{
  "channels": {
    "qq": {
      "enabled": true,
      "appId": "${QQ_APP_ID}",
      "secret": "${QQ_SECRET}",
      "msgFormat": "plain",
      "allowFrom": []
    }
  }
}
```

配置说明：
- `msgFormat` — 消息格式：`"plain"` 纯文本，`"markdown"` Markdown 格式
- `allowFrom` — 可选的用户 ID 白名单

```bash
export QQ_APP_ID="你的AppID"
export QQ_SECRET="你的Secret"
pip install qq-botpy
```

> **排查提示：**
> - QQ 机器人需要通过审核才能使用，沙箱环境可先在测试频道中调试。
> - 群消息需要 @机器人 才会触发。
> - 提示 `invalid appid`？确认 App ID 和 Secret 正确，且机器人已上线。

### 完整多通道配置示例

以下展示同时启用多个中国平台通道的 `~/.ultrabot/config.json`：

```json
{
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "${TELEGRAM_BOT_TOKEN}"
    },
    "discord": {
      "enabled": true,
      "token": "${DISCORD_BOT_TOKEN}"
    },
    "feishu": {
      "enabled": true,
      "appId": "${FEISHU_APP_ID}",
      "appSecret": "${FEISHU_APP_SECRET}"
    },
    "wecom": {
      "enabled": false,
      "botId": "${WECOM_BOT_ID}",
      "secret": "${WECOM_SECRET}"
    },
    "qq": {
      "enabled": false,
      "appId": "${QQ_APP_ID}",
      "secret": "${QQ_SECRET}"
    }
  }
}
```

智能体和消息总线完全不感知底层平台 — 用户在任何通道发送的消息
都经过相同的处理管道。

### 本课成果

四个中国消息平台通道 — 企业微信（WebSocket SDK）、微信
（HTTP 长轮询 + AES 加密）、飞书（在专用线程中运行 SDK WebSocket）
和 QQ（botpy SDK）— 全部实现相同的 `BaseChannel` 接口。
智能体和消息总线完全不感知底层平台。

---

## 本课使用的 Python 知识

### `from __future__ import annotations`（延迟类型注解求值）

这是一个特殊的导入语句，让 Python 将所有类型注解当作字符串处理，而不是立即求值。这样你就可以在类型注解中使用尚未定义的类名，也能让代码在较低版本的 Python 上兼容新的类型语法。

```python
from __future__ import annotations

class MyClass:
    def method(self) -> MyClass:  # 不会报错，因为注解被延迟求值
        return self
```

**为什么在本课中使用：** 四个通道类中的方法签名引用了 `OutboundMessage`、`MessageBus` 等尚未导入的类型，延迟求值避免了循环导入和前向引用错误。

### `async def` / `await`（异步编程）

`async def` 定义一个协程函数，`await` 用于等待另一个协程完成。异步编程让程序在等待网络 I/O 时不会阻塞，从而同时处理多个任务。

```python
import asyncio

async def fetch_data():
    await asyncio.sleep(1)  # 模拟网络请求，不阻塞其他任务
    return "数据"

async def main():
    result = await fetch_data()
    print(result)

asyncio.run(main())
```

**为什么在本课中使用：** 每个通道都需要同时监听消息和发送回复，异步编程让多个通道可以在同一个事件循环中高效并发运行，而不需要为每个通道创建独立的线程。

### `collections.OrderedDict`（有序字典用作消息去重缓存）

`OrderedDict` 是一种记住插入顺序的字典。在本课中它被用作有界的去重缓存——新消息 ID 从末尾添加，当缓存超过上限时从头部淘汰最旧的条目。

```python
from collections import OrderedDict

cache = OrderedDict()

def is_duplicate(msg_id):
    if msg_id in cache:
        return True
    cache[msg_id] = None
    while len(cache) > 1000:  # 最多保留 1000 条
        cache.popitem(last=False)  # 淘汰最旧的
    return False
```

**为什么在本课中使用：** 消息平台可能重复推送同一条消息（网络重连、SDK 重试等），`OrderedDict` 提供了一种简单高效的去重机制，同时自动淘汰旧记录以控制内存使用。

### `TYPE_CHECKING`（条件导入模式）

`typing.TYPE_CHECKING` 是一个在运行时为 `False`、仅在类型检查工具（如 mypy）运行时为 `True` 的常量。配合 `if TYPE_CHECKING:` 使用，可以只在类型检查时导入某些模块，避免运行时的循环导入。

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from heavy_module import HeavyClass  # 仅类型检查时导入

def process(obj: "HeavyClass") -> None:  # 用字符串注解
    pass
```

**为什么在本课中使用：** 通道类需要引用 `OutboundMessage` 和 `MessageBus` 的类型，但运行时不应该立刻导入这些模块（避免循环依赖和不必要的加载），所以使用 `TYPE_CHECKING` 模式。

### `importlib.util.find_spec()`（可选依赖检测）

`importlib.util.find_spec()` 检查某个模块是否可被导入，而不实际导入它。返回 `None` 表示该模块未安装。

```python
import importlib.util

if importlib.util.find_spec("requests") is not None:
    print("requests 库已安装")
else:
    print("requests 库未安装")
```

**为什么在本课中使用：** 四个通道各依赖不同的 SDK（`wecom-aibot-sdk`、`lark-oapi`、`botpy` 等），用户不一定全部安装。通过 `find_spec()` 检测 SDK 是否存在，未安装时给出友好的错误提示而不是崩溃。

### 类继承与 `super().__init__()`（面向对象编程）

Python 的类继承让子类可以复用父类的代码。`super().__init__()` 调用父类的构造函数，确保父类的初始化逻辑被正确执行。

```python
class Animal:
    def __init__(self, name):
        self.name = name

class Dog(Animal):
    def __init__(self, name, breed):
        super().__init__(name)  # 调用 Animal 的 __init__
        self.breed = breed
```

**为什么在本课中使用：** 四个通道类（`WecomChannel`、`WeixinChannel`、`FeishuChannel`、`QQChannel`）都继承自 `BaseChannel`，共享同一个接口。`super().__init__(config, bus)` 确保基类的配置和消息总线被正确初始化。

### `@property`（属性装饰器）

`@property` 让一个方法看起来像属性一样被访问（不需要加括号调用），常用于提供只读属性或计算属性。

```python
class Circle:
    def __init__(self, radius):
        self._radius = radius

    @property
    def area(self):
        return 3.14159 * self._radius ** 2

c = Circle(5)
print(c.area)  # 像访问属性一样，不需要 c.area()
```

**为什么在本课中使用：** 每个通道类用 `@property` 定义 `name` 属性（如 `"wecom"`、`"feishu"`），让外部代码可以用 `channel.name` 直接获取通道名称，简洁且只读。

### `threading.Thread`（多线程）

`threading.Thread` 创建一个新的操作系统线程来并行执行代码。当某个第三方库有自己的事件循环，与主程序的 asyncio 循环冲突时，可以在单独的线程中运行它。

```python
import threading

def background_work():
    print("在后台线程中运行")

t = threading.Thread(target=background_work, daemon=True)
t.start()  # 启动线程
```

**为什么在本课中使用：** 飞书的 `lark-oapi` SDK 自带事件循环，与 ultrabot 主循环冲突。解决方案是在一个 `daemon=True` 的后台线程中运行飞书 SDK 的 WebSocket 客户端。

### `asyncio.run_coroutine_threadsafe()`（跨线程调度协程）

当你在一个普通线程中需要调用主线程的异步函数时，`run_coroutine_threadsafe()` 可以将协程安全地提交到另一个线程的事件循环中执行。

```python
import asyncio, threading

async def async_handler(data):
    print(f"处理: {data}")

loop = asyncio.new_event_loop()

def sync_callback(data):
    # 从普通线程中安全调度到 asyncio 事件循环
    asyncio.run_coroutine_threadsafe(async_handler(data), loop)
```

**为什么在本课中使用：** 飞书 SDK 在后台线程中收到消息后触发同步回调 `_on_message_sync`，需要将异步处理任务安全地提交回主 asyncio 事件循环，`run_coroutine_threadsafe` 正是桥接同步线程和异步循环的关键。

### 工厂函数动态创建类（`_make_bot_class`）

Python 允许在函数内部定义类，并将外部变量"绑定"到这个类中。这种工厂模式可以动态创建与特定实例关联的子类。

```python
def make_handler(greeting):
    class Handler:
        def handle(self):
            print(greeting)  # 使用外层函数的变量
    return Handler

MyHandler = make_handler("你好")
h = MyHandler()
h.handle()  # 输出: 你好
```

**为什么在本课中使用：** QQ 的 `botpy` SDK 要求用户子类化 `botpy.Client` 来处理事件。`_make_bot_class(channel)` 在函数内部创建一个绑定到当前 `QQChannel` 实例的子类，这样事件处理方法就能直接访问通道实例。

### `httpx.AsyncClient`（异步 HTTP 客户端）

`httpx` 是一个现代的 Python HTTP 库，`AsyncClient` 是其异步版本，支持连接池、超时配置、重定向跟随等功能。

```python
import httpx

async def fetch():
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get("https://example.com")
        return response.text
```

**为什么在本课中使用：** 微信通道使用 HTTP 长轮询而非 WebSocket，需要反复发起 HTTP 请求。`httpx.AsyncClient` 提供了连接复用和可配置的超时（45 秒读取、30 秒连接），非常适合长轮询场景。

### `try` / `except ImportError`（优雅降级的可选导入）

通过捕获 `ImportError` 异常来尝试导入一个库，如果失败则回退到替代方案。这让代码可以在多种环境下工作。

```python
try:
    from fast_json import loads  # 优先使用更快的库
except ImportError:
    from json import loads       # 回退到标准库
```

**为什么在本课中使用：** 微信通道的 AES 解密同时支持 `pycryptodome` 和 `cryptography` 两个加密库。代码先尝试导入 `Crypto.Cipher`，如果不存在就回退到 `cryptography`，用户只需安装任一个即可。

### `bytes` 类型与加密操作

`bytes` 是 Python 中表示二进制数据的类型，在加密、网络传输和文件操作中广泛使用。`base64` 模块用于在二进制数据和文本之间转换。

```python
import base64

raw_key = b"0123456789abcdef"       # 16 字节的密钥
b64_key = base64.b64encode(raw_key)  # 编码为 base64
decoded = base64.b64decode(b64_key)  # 解码回原始字节
assert decoded == raw_key
```

**为什么在本课中使用：** 微信媒体文件使用 AES-128-ECB 加密，密钥以 base64 编码存储。解密过程需要将 base64 密钥解码为 16 字节的 `bytes`，然后用于 AES 解密操作。

### `importlib.import_module()`（动态模块导入）

`importlib.import_module()` 可以在运行时根据字符串名称导入模块，而不需要在代码顶部写死 `import` 语句。

```python
import importlib

mod = importlib.import_module("json")
data = mod.loads('{"key": "value"}')
print(data)  # {'key': 'value'}
```

**为什么在本课中使用：** 测试代码需要验证四个通道模块是否可以正确导入和加载。通过 `importlib.import_module("ultrabot.channels.wecom")` 动态导入模块，配合 `hasattr` 检查类是否存在，实现了灵活的模块可用性测试。

### `issubclass()` 与 `hasattr()`（内省机制）

`issubclass(A, B)` 检查类 A 是否是类 B 的子类；`hasattr(obj, name)` 检查对象是否拥有某个属性或方法。这些是 Python 的内省（introspection）功能。

```python
class Base:
    pass

class Child(Base):
    pass

print(issubclass(Child, Base))  # True
print(hasattr(Child, "__init__"))  # True
```

**为什么在本课中使用：** 测试代码用 `issubclass(WeixinChannel, BaseChannel)` 验证通道类确实继承了基类接口，用 `hasattr(mod, "WecomChannel")` 验证模块中存在预期的类——这是接口契约的自动化验证。
