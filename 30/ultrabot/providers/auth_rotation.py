# ultrabot/providers/auth_rotation.py
"""API 密钥轮换 -- 跨多个密钥的轮询式轮换和冷却管理。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from loguru import logger


class CredentialState(Enum):
    """凭证状态。"""
    ACTIVE = "active"
    COOLDOWN = "cooldown"
    FAILED = "failed"


@dataclass
class AuthProfile:
    """带有状态追踪的单个 API 凭证。

    ACTIVE → COOLDOWN（遇到速率限制时） → ACTIVE（冷却期过后）
    ACTIVE → FAILED（连续失败 3 次后）
    """
    key: str
    state: CredentialState = CredentialState.ACTIVE
    cooldown_until: float = 0.0
    consecutive_failures: int = 0
    max_failures: int = 3

    @property
    def is_available(self) -> bool:
        """检查凭证是否可用。"""
        if self.state == CredentialState.FAILED:
            return False
        if self.state == CredentialState.COOLDOWN:
            if time.time() >= self.cooldown_until:
                self.state = CredentialState.ACTIVE
                self.consecutive_failures = 0
                return True
            return False
        return True

    def record_failure(self, cooldown_seconds: float = 60.0) -> None:
        """记录一次失败。"""
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.max_failures:
            self.state = CredentialState.FAILED
            logger.warning("Key {}... marked as FAILED after {} failures",
                         self.key[:8], self.consecutive_failures)
        else:
            self.state = CredentialState.COOLDOWN
            self.cooldown_until = time.time() + cooldown_seconds
            logger.debug("Key {}... in cooldown until {}",
                        self.key[:8], self.cooldown_until)

    def record_success(self) -> None:
        """记录一次成功。"""
        self.consecutive_failures = 0
        self.state = CredentialState.ACTIVE

    def reset(self) -> None:
        """重置凭证状态。"""
        self.state = CredentialState.ACTIVE
        self.cooldown_until = 0.0
        self.consecutive_failures = 0


class AuthRotator:
    """跨多个 API 密钥的轮询式轮换。"""

    def __init__(self, keys: list[str], cooldown_seconds: float = 60.0):
        # 去重且过滤空字符串
        seen: set[str] = set()
        unique_keys: list[str] = []
        for k in keys:
            if k and k not in seen:
                seen.add(k)
                unique_keys.append(k)
        self._profiles: list[AuthProfile] = [AuthProfile(key=k) for k in unique_keys]
        self._current_index: int = 0
        self._cooldown_seconds: float = cooldown_seconds

    @property
    def profile_count(self) -> int:
        """返回唯一密钥的数量。"""
        return len(self._profiles)

    def get_next_key(self) -> str | None:
        """获取下一个可用密钥。所有密钥耗尽时返回 None。"""
        if not self._profiles:
            return None
        for _ in range(len(self._profiles)):
            profile = self._profiles[self._current_index]
            self._current_index = (self._current_index + 1) % len(self._profiles)
            if profile.is_available:
                return profile.key
        # 最后手段：重置失败的密钥
        for profile in self._profiles:
            if profile.state == CredentialState.FAILED:
                profile.reset()
                return profile.key
        return None

    def record_failure(self, key: str) -> None:
        """记录指定密钥的一次失败。"""
        for profile in self._profiles:
            if profile.key == key:
                profile.record_failure(self._cooldown_seconds)
                return

    def record_success(self, key: str) -> None:
        """记录指定密钥的一次成功。"""
        for profile in self._profiles:
            if profile.key == key:
                profile.record_success()
                return


async def execute_with_rotation(rotator: AuthRotator, execute: Callable,
                                 is_rate_limit: Callable | None = None) -> Any:
    """使用自动密钥轮换执行异步函数，失败时自动切换。"""
    last_error = None
    for _ in range(rotator.profile_count or 1):
        key = rotator.get_next_key()
        if key is None:
            break
        try:
            result = await execute(key)
            rotator.record_success(key)
            return result
        except Exception as e:
            last_error = e
            if is_rate_limit and is_rate_limit(e):
                rotator.record_failure(key)
            else:
                raise
    if last_error:
        raise last_error
    raise RuntimeError("No API keys available")
