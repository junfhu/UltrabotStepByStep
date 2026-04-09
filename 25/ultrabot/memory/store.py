"""基于向量的记忆存储，用于长期知识检索。

使用 SQLite 配合 FTS5 进行关键词搜索，可选 sqlite-vec 进行
语义向量搜索。当向量扩展不可用时回退到纯关键词模式。
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class MemoryEntry:
    """单条记忆条目。"""
    id: str
    content: str
    source: str = ""                    # 例如 "session:telegram:123"
    timestamp: float = field(default_factory=time.time)
    embedding: list[float] | None = None  # 保留给未来的向量搜索
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0                  # 搜索时填充


@dataclass
class SearchResult:
    """记忆搜索的结果。"""
    entries: list[MemoryEntry] = field(default_factory=list)
    query: str = ""
    method: str = ""        # "fts"、"vector"、"hybrid"
    elapsed_ms: float = 0.0

class MemoryStore:
    """基于 SQLite 的记忆存储，带 FTS5 关键词搜索。

    参数：
        db_path: SQLite 数据库文件路径。
        temporal_decay_half_life_days: 时间衰减评分的半衰期。
            越旧的记忆得分越低。0 = 无衰减。
    """

    def __init__(self, db_path: Path, temporal_decay_half_life_days: float = 30.0) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._half_life = temporal_decay_half_life_days
        self._conn = sqlite3.connect(str(self.db_path))
        self._init_db()
        logger.info("MemoryStore initialised at {}", db_path)

    def _init_db(self) -> None:
        """如果表不存在则创建。"""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                source TEXT DEFAULT '',
                timestamp REAL NOT NULL,
                metadata TEXT DEFAULT '{}',
                content_hash TEXT
            );

            -- FTS5 虚拟表，用于全文搜索（内置 BM25 排名）
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
                USING fts5(content, source, content='memories', content_rowid='rowid');

            -- 触发器自动保持 FTS 索引同步
            CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                INSERT INTO memories_fts(rowid, content, source)
                VALUES (new.rowid, new.content, new.source);
            END;

            CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, content, source)
                VALUES ('delete', old.rowid, old.content, old.source);
            END;

            CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, content, source)
                VALUES ('delete', old.rowid, old.content, old.source);
                INSERT INTO memories_fts(rowid, content, source)
                VALUES (new.rowid, new.content, new.source);
            END;

            CREATE INDEX IF NOT EXISTS idx_memories_source ON memories(source);
            CREATE INDEX IF NOT EXISTS idx_memories_timestamp ON memories(timestamp);
            CREATE INDEX IF NOT EXISTS idx_memories_hash ON memories(content_hash);
        """)
        self._conn.commit()

    def add(
        self,
        content: str,
        source: str = "",
        entry_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        timestamp: float | None = None,
    ) -> str:
        """添加一条记忆条目。返回条目 ID。

        通过内容哈希去重，避免存储相同的条目。
        """
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

        # 检查是否重复
        existing = self._conn.execute(
            "SELECT id FROM memories WHERE content_hash = ?", (content_hash,)
        ).fetchone()
        if existing:
            return existing[0]  # 已存储 — 返回已有 ID

        if entry_id is None:
            entry_id = f"mem_{content_hash}_{int(time.time())}"

        self._conn.execute(
            "INSERT INTO memories (id, content, source, timestamp, metadata, content_hash)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (entry_id, content, source, timestamp or time.time(),
             json.dumps(metadata or {}), content_hash),
        )
        self._conn.commit()
        return entry_id

    def search(
        self,
        query: str,
        limit: int = 10,
        source_filter: str | None = None,
        min_score: float = 0.0,
    ) -> SearchResult:
        """使用 FTS5 关键词搜索记忆，带时间衰减。"""
        start_time = time.time()

        try:
            sql = """
                SELECT m.id, m.content, m.source, m.timestamp, m.metadata,
                       rank AS bm25_score
                FROM memories_fts f
                JOIN memories m ON m.rowid = f.rowid
                WHERE memories_fts MATCH ?
            """
            params: list[Any] = [query]
            if source_filter:
                sql += " AND m.source LIKE ?"
                params.append(f"%{source_filter}%")
            sql += " ORDER BY rank LIMIT ?"
            params.append(limit * 3)  # 多取一些用于衰减后重排序
            rows = self._conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            # FTS 查询语法错误 — 回退到 LIKE
            rows = self._conn.execute(
                "SELECT id, content, source, timestamp, metadata, 1.0"
                " FROM memories WHERE content LIKE ? LIMIT ?",
                (f"%{query}%", limit * 3),
            ).fetchall()

        entries = []
        now = time.time()
        for row in rows:
            entry_id, content, source, timestamp, metadata_str, bm25 = row
            age_days = (now - timestamp) / 86400
            decay = self._temporal_decay(age_days)
            score = abs(bm25) * decay

            if score < min_score:
                continue

            entries.append(MemoryEntry(
                id=entry_id, content=content, source=source,
                timestamp=timestamp,
                metadata=json.loads(metadata_str) if metadata_str else {},
                score=score,
            ))

        entries.sort(key=lambda e: e.score, reverse=True)
        entries = entries[:limit]

        elapsed = (time.time() - start_time) * 1000
        return SearchResult(entries=entries, query=query, method="fts", elapsed_ms=elapsed)

    def _temporal_decay(self, age_days: float) -> float:
        """指数时间衰减：score * exp(-lambda * age)。"""
        if self._half_life <= 0:
            return 1.0
        lam = math.log(2) / self._half_life
        return math.exp(-lam * age_days)

    def delete(self, entry_id: str) -> bool:
        cursor = self._conn.execute("DELETE FROM memories WHERE id = ?", (entry_id,))
        self._conn.commit()
        return cursor.rowcount > 0

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM memories").fetchone()
        return row[0] if row else 0

    def clear(self, source: str | None = None) -> int:
        if source:
            cursor = self._conn.execute("DELETE FROM memories WHERE source LIKE ?",
                                        (f"%{source}%",))
        else:
            cursor = self._conn.execute("DELETE FROM memories")
        self._conn.commit()
        return cursor.rowcount

    def close(self) -> None:
        self._conn.close()

class ContextEngine:
    """可插拔的上下文引擎，用于智能上下文组装。

    管理上下文的生命周期：摄入消息、为 LLM 调用组装上下文，
    以及压缩旧上下文以节省 token。
    """

    def __init__(self, memory_store: MemoryStore | None = None,
                 token_budget: int = 128000) -> None:
        self._memory = memory_store
        self._token_budget = token_budget

    def ingest(self, session_key: str, message: dict[str, Any]) -> None:
        """将消息摄入长期记忆。

        仅摄入足够长的 user/assistant 消息。
        """
        if self._memory is None:
            return
        content = message.get("content", "")
        role = message.get("role", "")
        if role not in ("user", "assistant"):
            return
        if not content or len(content) < 20:
            return
        self._memory.add(content=content, source=f"session:{session_key}")

    def retrieve_context(self, query: str, session_key: str = "",
                         max_tokens: int = 4000) -> str:
        """从记忆中检索与查询相关的上下文。"""
        if self._memory is None:
            return ""
        results = self._memory.search(query, limit=10)
        if not results.entries:
            return ""

        context_parts = []
        token_count = 0
        for entry in results.entries:
            entry_tokens = len(entry.content) // 4  # 约 4 字符 = 1 token
            if token_count + entry_tokens > max_tokens:
                break
            context_parts.append(entry.content)
            token_count += entry_tokens

        if not context_parts:
            return ""
        return "Relevant context from memory:\n" + "\n---\n".join(context_parts)

    def compact(self, session_messages: list[dict[str, Any]],
                max_tokens: int | None = None) -> list[dict[str, Any]]:
        """压缩会话消息以适应 token 预算。

        保留系统提示词和最近的消息。
        """
        if max_tokens is None:
            max_tokens = self._token_budget

        total = sum(len(str(m.get("content", ""))) // 4 for m in session_messages)
        if total <= max_tokens:
            return session_messages

        result = []
        if session_messages and session_messages[0].get("role") == "system":
            result.append(session_messages[0])
            session_messages = session_messages[1:]

        keep_recent = min(10, len(session_messages))
        recent = session_messages[-keep_recent:]
        old = session_messages[:-keep_recent]

        if old:
            summary_parts = []
            for msg in old:
                role = msg.get("role", "unknown")
                content = str(msg.get("content", ""))[:200]
                if content:
                    summary_parts.append(f"[{role}]: {content}")
            if summary_parts:
                summary = "Previous conversation summary:\n" + "\n".join(summary_parts[-20:])
                result.append({"role": "system", "content": summary})

        result.extend(recent)
        return result
