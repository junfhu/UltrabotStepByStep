# ultrabot/channels/weixin.py
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
