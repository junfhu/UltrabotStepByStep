# ultrabot/providers/auth_rotation.py  （关键摘录）

class AuthProfile:
    """带有状态追踪的单个 API 凭证。
    
    ACTIVE → COOLDOWN（遇到速率限制时） → ACTIVE（冷却期过后）
    ACTIVE → FAILED（连续失败 3 次后）
    """
    key: str
    state: CredentialState = CredentialState.ACTIVE
    cooldown_until: float = 0.0
    consecutive_failures: int = 0

class AuthRotator:
    """跨多个 API 密钥的轮询式轮换。"""
    
    def get_next_key(self) -> str | None:
        """获取下一个可用密钥。所有密钥耗尽时返回 None。"""
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

async def execute_with_rotation(rotator, execute, is_rate_limit=None):
    """使用自动密钥轮换执行异步函数，失败时自动切换。"""
    ...
