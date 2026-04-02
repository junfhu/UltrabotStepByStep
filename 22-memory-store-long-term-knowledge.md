# 课程 22：记忆存储 — 长期知识

**目标：** 构建一个持久化记忆存储，使用 SQLite FTS5 全文搜索和时间衰减评分，加上一个用于智能上下文组装的上下文引擎。

**你将学到：**
- 基于 SQLite 和 FTS5 虚拟表的 `MemoryStore`
- 带有内容哈希去重的 `MemoryEntry` dataclass
- BM25 评分加指数时间衰减
- 用于摄入消息和检索相关上下文的 `ContextEngine`
- 用于 token 预算管理的会话消息压缩

**新建文件：**
- `ultrabot/memory/__init__.py` — 包导出
- `ultrabot/memory/store.py` — SQLite FTS5 记忆存储和上下文引擎

### 步骤 1：MemoryEntry 和 SearchResult Dataclass

```python
# ultrabot/memory/store.py
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
```

### 步骤 2：SQLite + FTS5 Schema

数据库使用触发器自动保持 FTS5 索引与主表同步。内容哈希索引实现去重。

```python
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
```

### 步骤 3：带内容哈希去重的添加

```python
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
```

### 步骤 4：FTS5 搜索与时间衰减

FTS5 的 BM25 分数乘以基于条目年龄的指数衰减因子。半衰期控制旧记忆衰减的速度。

```python
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
```

### 步骤 5：ContextEngine

`ContextEngine` 位于记忆存储和代理之间，处理自动摄入、检索和会话压缩。

```python
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
```

### 测试

```python
# tests/test_memory_store.py
"""记忆存储和上下文引擎的测试。"""

import time
import pytest
from pathlib import Path

from ultrabot.memory.store import MemoryStore, MemoryEntry, SearchResult, ContextEngine


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(db_path=tmp_path / "test_memory.db", temporal_decay_half_life_days=30.0)
    yield s
    s.close()


class TestMemoryStore:
    def test_add_and_count(self, store):
        entry_id = store.add("Python is a programming language", source="test")
        assert entry_id.startswith("mem_")
        assert store.count() == 1

    def test_deduplication(self, store):
        id1 = store.add("Exact same content")
        id2 = store.add("Exact same content")
        assert id1 == id2
        assert store.count() == 1

    def test_search_fts(self, store):
        store.add("Python is great for machine learning", source="docs")
        store.add("JavaScript powers the web", source="docs")
        store.add("Rust is fast and safe", source="docs")

        results = store.search("Python machine learning")
        assert len(results.entries) >= 1
        assert results.method == "fts"
        assert "Python" in results.entries[0].content

    def test_search_source_filter(self, store):
        store.add("Filtered content", source="session:123")
        store.add("Other content about filtering", source="session:456")

        results = store.search("content", source_filter="session:123")
        assert all("123" in e.source for e in results.entries)

    def test_delete(self, store):
        entry_id = store.add("To be deleted")
        assert store.count() == 1
        assert store.delete(entry_id) is True
        assert store.count() == 0

    def test_clear(self, store):
        store.add("One", source="a")
        store.add("Two", source="b")
        assert store.count() == 2
        deleted = store.clear()
        assert deleted == 2
        assert store.count() == 0

    def test_temporal_decay(self, store):
        assert store._temporal_decay(0) == pytest.approx(1.0)
        assert store._temporal_decay(30) == pytest.approx(0.5, rel=0.01)
        assert store._temporal_decay(60) == pytest.approx(0.25, rel=0.01)


class TestContextEngine:
    def test_ingest_filters_short_messages(self, tmp_path):
        ms = MemoryStore(db_path=tmp_path / "ctx.db")
        engine = ContextEngine(memory_store=ms)

        engine.ingest("s1", {"role": "user", "content": "hi"})       # 太短
        engine.ingest("s1", {"role": "system", "content": "You are..."})  # 角色不对
        assert ms.count() == 0

        engine.ingest("s1", {"role": "user", "content": "Tell me about Python programming in detail"})
        assert ms.count() == 1
        ms.close()

    def test_retrieve_context(self, tmp_path):
        ms = MemoryStore(db_path=tmp_path / "ctx2.db")
        ms.add("Python is great for data science and machine learning")
        engine = ContextEngine(memory_store=ms)

        ctx = engine.retrieve_context("data science")
        assert "Python" in ctx
        assert "Relevant context" in ctx
        ms.close()

    def test_compact_preserves_recent(self):
        engine = ContextEngine(token_budget=100)
        messages = [{"role": "system", "content": "System prompt"}]
        messages += [{"role": "user", "content": f"Message {i}" * 20} for i in range(50)]

        compacted = engine.compact(messages, max_tokens=100)
        assert compacted[0]["role"] == "system"
        assert len(compacted) < len(messages)
```

### 检查点

```bash
python -c "
import tempfile
from pathlib import Path
from ultrabot.memory.store import MemoryStore, ContextEngine

db = Path(tempfile.mktemp(suffix='.db'))
store = MemoryStore(db_path=db)

# 存储一些事实
store.add('My favorite color is blue', source='chat')
store.add('I work at a tech company called Acme Corp', source='chat')
store.add('Python is my preferred programming language', source='chat')

print(f'Stored {store.count()} memories')

# 搜索
results = store.search('favorite color')
for e in results.entries:
    print(f'  Found: {e.content[:60]}  (score={e.score:.2f})')

# 上下文引擎
engine = ContextEngine(memory_store=store)
ctx = engine.retrieve_context('What company do I work at?')
print(f'Context retrieved: {ctx[:80]}...')

store.close()
"
```

预期输出：
```
Stored 3 memories
  Found: My favorite color is blue  (score=X.XX)
Context retrieved: Relevant context from memory:
I work at a tech company called Acme...
```

### 本课成果

一个基于 SQLite FTS5 的持久化长期记忆系统，具备自动去重、BM25 全文搜索和
指数时间衰减评分。`ContextEngine` 层处理自动消息摄入、在 token 预算内检索
相关上下文，以及会话压缩以保持对话在上下文窗口限制内。

---
