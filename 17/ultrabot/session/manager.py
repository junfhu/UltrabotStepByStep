# ultrabot/session/manager.py
"""会话管理 -- 持久化、TTL 过期和上下文窗口修剪。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger


# ------------------------------------------------------------------
# Session 数据类
# ------------------------------------------------------------------

@dataclass
class Session:
    """单个对话会话。

    Attributes:
        session_id: 唯一标识符（通常为 ``{channel}:{chat_id}``）。
        messages:   发送给/接收自 LLM 的有序消息字典列表。
        created_at: 会话首次创建的 UTC 时间戳。
        last_active: 最近一次活动的 UTC 时间戳。
        metadata:   任意的会话级键值存储。
        token_count: 所有消息的总 token 数量的持续估算值。
    """

    session_id: str
    messages: list[dict] = field(default_factory=list)
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    last_active: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    metadata: dict = field(default_factory=dict)
    token_count: int = 0

    @staticmethod
    def _estimate_tokens(content: str) -> int:
        """粗略的 token 估算：约 4 个字符对应 1 个 token。"""
        return max(len(content) // 4, 1)

    def add_message(self, msg: dict) -> None:
        """追加一条消息并更新记录。"""
        self.messages.append(msg)
        content = msg.get("content", "")
        self.token_count += self._estimate_tokens(content)
        self.last_active = datetime.now(timezone.utc)

    def get_messages(self) -> list[dict]:
        """返回消息历史的浅拷贝。"""
        return list(self.messages)

    def clear(self) -> None:
        """清除消息历史并重置 token 计数器。"""
        self.messages.clear()
        self.token_count = 0
        self.last_active = datetime.now(timezone.utc)

    def trim(self, max_tokens: int) -> int:
        """丢弃最旧的非系统消息，直到适应 *max_tokens* 预算。

        返回被移除的消息数量。
        """
        removed = 0
        while self.token_count > max_tokens and self.messages:
            # 永远不要修剪系统提示词（始终在索引 0）。
            if self.messages[0].get("role") == "system":
                if len(self.messages) <= 1:
                    break                        # 只剩系统提示词了
                oldest = self.messages.pop(1)    # 改为移除次旧的消息
            else:
                oldest = self.messages.pop(0)

            tokens = self._estimate_tokens(oldest.get("content", ""))
            self.token_count = max(self.token_count - tokens, 0)
            removed += 1

        if removed:
            logger.debug(
                "Trimmed {} message(s) from session {} (tokens now ~{})",
                removed, self.session_id, self.token_count,
            )
        return removed

    def to_dict(self) -> dict:
        """序列化为适合 JSON 的纯字典。"""
        data = asdict(self)
        data["created_at"] = self.created_at.isoformat()
        data["last_active"] = self.last_active.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict) -> Session:
        """从字典重建一个 Session（例如从磁盘加载）。"""
        data = dict(data)                             # 不修改调用者的数据
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        data["last_active"] = datetime.fromisoformat(data["last_active"])
        return cls(**data)


class SessionManager:
    """拥有、持久化和垃圾回收会话的注册中心。

    Parameters:
        data_dir:  根数据目录。会话保存在 data_dir/sessions/ 下。
        ttl_seconds: 会话空闲多久后有资格被清理。
        max_sessions: 内存中会话数量的上限（LRU 淘汰）。
        context_window_tokens: 每个会话的最大 token 预算。
    """

    def __init__(
        self,
        data_dir: Path,
        ttl_seconds: int = 3600,
        max_sessions: int = 1000,
        context_window_tokens: int = 65536,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self.context_window_tokens = context_window_tokens

        self._sessions_dir = self.data_dir / "sessions"
        self._sessions_dir.mkdir(parents=True, exist_ok=True)

        self._sessions: dict[str, Session] = {}
        self._lock = asyncio.Lock()                   # 守护所有变更操作

        logger.info(
            "SessionManager initialised | data_dir={} ttl={}s max={}",
            self._sessions_dir, ttl_seconds, max_sessions,
        )

    def _session_path(self, session_key: str) -> Path:
        """返回 *session_key* 在磁盘上的路径。"""
        safe_name = session_key.replace("/", "_").replace("\\", "_")
        return self._sessions_dir / f"{safe_name}.json"

    async def get_or_create(self, session_key: str) -> Session:
        """获取已有会话或创建新会话。

        1. 检查内存缓存。
        2. 尝试从磁盘加载。
        3. 创建全新的会话。
        """
        async with self._lock:
            if session_key in self._sessions:
                session = self._sessions[session_key]
                session.last_active = datetime.now(timezone.utc)
                return session

            # 尝试从磁盘加载。
            session = await self._load_unlocked(session_key)
            if session is not None:
                self._sessions[session_key] = session
                session.last_active = datetime.now(timezone.utc)
                logger.debug("Session loaded from disk: {}", session_key)
                return session

            # 创建新会话。
            session = Session(session_id=session_key)
            self._sessions[session_key] = session
            logger.info("New session created: {}", session_key)

            # 如果超出上限则淘汰最旧的会话。
            await self._enforce_max_sessions_unlocked()
            return session

    async def save(self, session_key: str) -> None:
        """将会话以 JSON 形式持久化到磁盘。"""
        async with self._lock:
            session = self._sessions.get(session_key)
            if session is None:
                return
            path = self._session_path(session_key)
            data = json.dumps(session.to_dict(), ensure_ascii=False, indent=2)
            path.write_text(data, encoding="utf-8")

    async def _load_unlocked(self, session_key: str) -> Session | None:
        """内部加载器（调用者必须持有 _lock）。"""
        path = self._session_path(session_key)
        if not path.exists():
            return None
        try:
            raw = path.read_text(encoding="utf-8")
            return Session.from_dict(json.loads(raw))
        except Exception:
            logger.exception("Failed to load session from {}", path)
            return None

    async def delete(self, session_key: str) -> None:
        """从内存和磁盘中删除会话。"""
        async with self._lock:
            self._sessions.pop(session_key, None)
            path = self._session_path(session_key)
            if path.exists():
                path.unlink()

    async def cleanup(self) -> int:
        """移除已超过 TTL 的会话。返回移除的数量。"""
        now = datetime.now(timezone.utc)
        removed = 0
        async with self._lock:
            expired = [
                key for key, s in self._sessions.items()
                if (now - s.last_active).total_seconds() > self.ttl_seconds
            ]
            for key in expired:
                del self._sessions[key]
                path = self._session_path(key)
                if path.exists():
                    path.unlink()
                removed += 1
        if removed:
            logger.info("{} expired session(s) cleaned up", removed)
        return removed

    async def _enforce_max_sessions_unlocked(self) -> None:
        """当超过 max_sessions 时淘汰最旧的不活跃会话。
        调用者必须持有 _lock。"""
        while len(self._sessions) > self.max_sessions:
            oldest_key = min(
                self._sessions,
                key=lambda k: self._sessions[k].last_active,
            )
            del self._sessions[oldest_key]
            logger.debug("Evicted oldest session: {}", oldest_key)
