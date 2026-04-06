# ultrabot/channels/pairing.py
"""PairingManager 为未知的私聊发送者生成审批码。
每个通道支持 OPEN、PAIRING 和 CLOSED 策略。"""

from __future__ import annotations

import json
import secrets
import string
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from loguru import logger


class PairingPolicy(Enum):
    """配对策略。"""
    OPEN = "open"
    PAIRING = "pairing"
    CLOSED = "closed"


@dataclass
class PairingRequest:
    """配对请求记录。"""
    sender_id: str
    channel: str
    code: str
    created_at: float = field(default_factory=time.time)
    approved: bool = False


class PairingManager:
    """管理发送者配对和审批。"""

    def __init__(
        self,
        data_dir: Path,
        default_policy: PairingPolicy = PairingPolicy.OPEN,
    ):
        self._data_dir = data_dir
        self._default_policy = default_policy
        self._approved: dict[str, set[str]] = {}  # channel -> set of sender_ids
        self._pending: dict[str, PairingRequest] = {}  # code -> PairingRequest
        self._sender_codes: dict[str, str] = {}  # "channel:sender_id" -> code
        logger.debug("PairingManager initialized with policy={}", default_policy.value)

    def _make_key(self, channel: str, sender_id: str) -> str:
        return f"{channel}:{sender_id}"

    def _generate_code(self) -> str:
        """生成 6 位审批码。"""
        return "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))

    def check_sender(self, channel: str, sender_id: str) -> tuple[bool, str | None]:
        """检查发送者是否被允许。

        Returns:
            (approved, code) — OPEN 策略返回 (True, None)；
            PAIRING 策略对未批准的发送者返回 (False, code)；
            CLOSED 策略返回 (False, None)。
        """
        if self._default_policy == PairingPolicy.OPEN:
            return True, None

        if self._default_policy == PairingPolicy.CLOSED:
            return False, None

        # PAIRING 模式
        if self.is_approved(channel, sender_id):
            return True, None

        # 检查是否已有待审批的 code
        key = self._make_key(channel, sender_id)
        if key in self._sender_codes:
            return False, self._sender_codes[key]

        # 生成新的审批码
        code = self._generate_code()
        self._pending[code] = PairingRequest(
            sender_id=sender_id, channel=channel, code=code,
        )
        self._sender_codes[key] = code
        logger.info("Pairing code {} generated for {}:{}", code, channel, sender_id)
        return False, code

    def approve_by_code(self, code: str) -> PairingRequest | None:
        """通过审批码批准发送者。"""
        request = self._pending.get(code)
        if request is None:
            return None

        request.approved = True
        channel_set = self._approved.setdefault(request.channel, set())
        channel_set.add(request.sender_id)
        del self._pending[code]
        key = self._make_key(request.channel, request.sender_id)
        self._sender_codes.pop(key, None)
        logger.info("Approved sender {}:{} via code {}", request.channel, request.sender_id, code)
        return request

    def is_approved(self, channel: str, sender_id: str) -> bool:
        """检查发送者是否已被批准。"""
        return sender_id in self._approved.get(channel, set())
