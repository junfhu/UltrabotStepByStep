# ultrabot/channels/weixin.py
"""使用 HTTP 长轮询的个人微信通道。"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from loguru import logger
from ultrabot.channels.base import BaseChannel

if TYPE_CHECKING:
    from ultrabot.bus.events import OutboundMessage
    from ultrabot.bus.queue import MessageBus


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

        self._running = True

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

    async def stop(self) -> None:
        """优雅关闭 HTTP 客户端。"""
        self._running = False
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        logger.info("WeixinChannel stopped")

    async def send(self, msg: "OutboundMessage") -> None:
        """向微信联系人发送文本消息。"""
        if self._client is None:
            logger.warning("Weixin client not initialized, cannot send")
            return

        chunks = _split_message(msg.content, 4000)
        for chunk in chunks:
            try:
                resp = await self._client.post(
                    f"{self._base_url}/cgi-bin/mmwebwx-bin/webwxsendmsg",
                    json={
                        "BaseRequest": {"Uin": "", "Sid": "", "Skey": ""},
                        "Msg": {
                            "Type": 1,
                            "Content": chunk,
                            "ToUserName": msg.chat_id,
                        },
                    },
                )
                resp.raise_for_status()
            except Exception as exc:
                logger.error("Weixin send error: {}", exc)
                raise

    def _load_state(self) -> bool:
        """尝试从磁盘加载已保存的登录状态。"""
        state_file = self._state_dir / "state.json"
        if not state_file.exists():
            return False
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            self._configured_token = data.get("token", "")
            logger.info("Weixin state loaded from {}", state_file)
            return bool(self._configured_token)
        except Exception as exc:
            logger.warning("Failed to load weixin state: {}", exc)
            return False

    async def _qr_login(self) -> bool:
        """执行二维码登录流程。"""
        if self._client is None:
            return False
        try:
            # 获取登录二维码的 UUID
            resp = await self._client.get(
                f"{self._base_url}/cgi-bin/mmwebwx-bin/login",
                params={"tip": 1},
            )
            resp.raise_for_status()
            logger.info("WeChat QR login: please scan the QR code")

            # 轮询等待扫码确认
            for _ in range(120):  # 最多等待 2 分钟
                if not self._running:
                    return False
                check_resp = await self._client.get(
                    f"{self._base_url}/cgi-bin/mmwebwx-bin/login",
                    params={"tip": 0},
                )
                body = check_resp.text
                if "window.code=200" in body:
                    logger.info("WeChat QR login successful")
                    # 保存状态
                    self._state_dir.mkdir(parents=True, exist_ok=True)
                    state_file = self._state_dir / "state.json"
                    state_file.write_text(
                        json.dumps({"token": self._configured_token}),
                        encoding="utf-8",
                    )
                    return True
                if "window.code=408" in body:
                    # 超时，继续轮询
                    await asyncio.sleep(1)
                    continue
                if "window.code=400" in body:
                    logger.error("WeChat QR code expired")
                    return False
                await asyncio.sleep(1)

            logger.error("WeChat QR login timed out")
            return False
        except Exception as exc:
            logger.error("WeChat QR login error: {}", exc)
            return False

    async def _poll_once(self) -> None:
        """执行一次长轮询请求并处理新消息。"""
        from ultrabot.bus.events import InboundMessage

        if self._client is None:
            return

        resp = await self._client.get(
            f"{self._base_url}/cgi-bin/mmwebwx-bin/synccheck",
        )
        resp.raise_for_status()

        # 检查是否有新消息
        body = resp.text
        if "selector:\"0\"" in body or "Selector:\"0\"" in body.replace(" ", ""):
            return  # 没有新消息

        # 同步获取新消息
        sync_resp = await self._client.get(
            f"{self._base_url}/cgi-bin/mmwebwx-bin/webwxsync",
        )
        sync_resp.raise_for_status()

        try:
            data = sync_resp.json()
        except Exception:
            return

        for msg in data.get("AddMsgList", []):
            msg_type = msg.get("MsgType", 0)
            if msg_type != 1:  # 只处理文本消息
                continue

            sender_id = msg.get("FromUserName", "")
            chat_id = msg.get("FromUserName", "")
            content = msg.get("Content", "")

            logger.info("Weixin message from {}: {}", sender_id, content[:50])
            await self.bus.publish(InboundMessage(
                channel=self.name,
                sender_id=sender_id,
                chat_id=chat_id,
                content=content,
            ))


def _split_message(text: str, limit: int) -> list[str]:
    """将长文本分割为不超过 limit 长度的块。

    保证 "".join(chunks) == text 且每个 chunk 长度 <= limit。
    """
    if not text:
        return [""]
    chunks: list[str] = []
    for i in range(0, len(text), limit):
        chunks.append(text[i:i + limit])
    return chunks


def _parse_aes_key(aes_key_b64: str) -> bytes:
    """解析 base64 编码的 AES 密钥，返回原始字节。"""
    return base64.b64decode(aes_key_b64)


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
