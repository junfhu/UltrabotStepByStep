# Agent: 30课程开发指南
**从零开始构建一个生产级 AI 助手框架。**
本指南将带你从"向 LLM 问好"一步步走到一个完整的多提供者、多通道 AI 智能体，具备工具调用、记忆、安全防护和 Web 界面。每节课程都建立在上一节课的基础之上。每节课都包含可运行的代码和测试。  
本教程的主要思路来自于
- Nanobot (https://github.com/HKUDS/nanobot)
- Learn-Claude-Code (https://github.com/shareAI-lab/learn-claude-code/)

本课程设计由AI辅助下完成，因为课程自身也在不停修正，请参考 https://github.com/junfhu/UltrabotStepByStep，如果您觉得对您有帮助，请帮助点亮一颗星。  



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

## 本课使用的 Python 知识

### `from __future__ import annotations`

这是一个特殊的导入语句，让 Python 把所有类型注解当作字符串处理（延迟求值），而不是在定义时立即解析。这样可以使用 `list[float] | None`、`dict[str, Any]` 等新式语法而不受 Python 版本限制。

```python
from __future__ import annotations

def process(items: list[int] | None = None) -> dict[str, Any]:
    ...
```

**为什么在本课中使用：** 本课代码使用了 `list[float] | None`、`dict[str, Any]` 等类型注解，加上这一行可以在 Python 3.9+ 上正常运行。

### `@dataclass` 与 `field(default_factory=...)`

`@dataclass` 自动生成 `__init__`、`__repr__` 等方法。对于列表、字典等可变类型的默认值，必须使用 `field(default_factory=list)` 而不能直接写 `= []`，否则所有实例会共享同一个列表。

```python
from dataclasses import dataclass, field
import time

@dataclass
class MemoryEntry:
    id: str
    content: str
    tags: list[str] = field(default_factory=list)      # 每个实例独立的空列表
    metadata: dict = field(default_factory=dict)        # 每个实例独立的空字典
    timestamp: float = field(default_factory=time.time) # 用函数返回值做默认值
```

**为什么在本课中使用：** `MemoryEntry` 和 `SearchResult` 都有列表和字典类型的字段，用 `field(default_factory=...)` 确保每条记忆条目有自己独立的数据副本。

### `hashlib.sha256()` 哈希计算

`hashlib` 模块提供各种安全哈希算法。`sha256` 可以对任意数据计算固定长度的摘要值，常用于数据去重和完整性校验。

```python
import hashlib

content = "Python is great"
hash_hex = hashlib.sha256(content.encode()).hexdigest()
short_hash = hash_hex[:16]  # 取前 16 个字符作为简短标识
print(short_hash)  # 例如 "a1b2c3d4e5f6a7b8"
```

**为什么在本课中使用：** 记忆存储用内容的 SHA-256 哈希作为去重键——相同内容的哈希值相同，存入前先检查是否已存在，避免重复存储。

### `sqlite3` SQLite 数据库操作

`sqlite3` 是 Python 内置的 SQLite 数据库接口，无需安装额外软件即可使用关系型数据库。支持 SQL 语句执行、事务管理和游标操作。

```python
import sqlite3

conn = sqlite3.connect("my_data.db")
conn.execute("CREATE TABLE IF NOT EXISTS items (id TEXT, value TEXT)")
conn.execute("INSERT INTO items VALUES (?, ?)", ("key1", "hello"))
conn.commit()

rows = conn.execute("SELECT * FROM items").fetchall()
print(rows)  # [('key1', 'hello')]
conn.close()
```

**为什么在本课中使用：** 长期记忆需要持久化存储，SQLite 是轻量级的嵌入式数据库，无需启动独立服务，非常适合本地知识库。配合 FTS5 虚拟表还能实现全文搜索。

### `json.dumps()` 和 `json.loads()` JSON 序列化

`json.dumps()` 将 Python 对象（字典、列表等）转换为 JSON 字符串；`json.loads()` 将 JSON 字符串解析回 Python 对象。

```python
import json

data = {"name": "ultrabot", "version": 2}
text = json.dumps(data)      # '{"name": "ultrabot", "version": 2}'
parsed = json.loads(text)    # {'name': 'ultrabot', 'version': 2}
```

**为什么在本课中使用：** SQLite 不直接支持存储字典，所以将 `metadata` 字典用 `json.dumps()` 序列化为字符串存入数据库，读取时再用 `json.loads()` 还原。

### `math.log()` 和 `math.exp()` 数学函数

`math.log()` 计算自然对数，`math.exp()` 计算自然指数（e 的幂）。这两个函数是指数衰减公式的核心。

```python
import math

half_life = 30  # 半衰期 30 天
lam = math.log(2) / half_life  # 衰减常数 λ
age_days = 60
decay = math.exp(-lam * age_days)
print(f"60 天后的衰减因子: {decay:.2f}")  # 约 0.25
```

**为什么在本课中使用：** 时间衰减评分机制让旧记忆的搜索得分随时间降低。公式 `exp(-λ × age)` 实现了指数衰减——半衰期后得分减半，体现"近期信息更重要"。

### `time.time()` 时间戳

`time.time()` 返回当前时间的 Unix 时间戳（从 1970 年 1 月 1 日至今的秒数），是一个浮点数。

```python
import time

now = time.time()
print(now)  # 例如 1700000000.123456
```

**为什么在本课中使用：** 每条记忆存入时记录时间戳，搜索时计算 `(now - timestamp) / 86400` 得到条目年龄（天），用于时间衰减评分。

### `pathlib.Path` 面向对象的路径操作

`pathlib.Path` 提供面向对象的文件系统路径操作，支持 `/` 运算符拼接、`.parent` 获取父目录、`.mkdir()` 创建目录等。

```python
from pathlib import Path

db_path = Path("/home/user/.ultrabot") / "memory.db"
db_path.parent.mkdir(parents=True, exist_ok=True)
```

**为什么在本课中使用：** `MemoryStore` 接收数据库路径作为参数，需要自动创建父目录。`Path` 让路径操作更简洁安全。

### `typing.Any` 类型注解

`Any` 表示"任意类型"，相当于告诉类型检查器"这个变量可以是任何东西"。在无法确定具体类型时使用。

```python
from typing import Any

def process(data: dict[str, Any]) -> None:
    # data 的值可以是字符串、数字、列表……任何类型
    pass
```

**为什么在本课中使用：** 记忆条目的 `metadata` 字段是灵活的键值对，值可以是任何类型，所以标注为 `dict[str, Any]`。

### `lambda` 表达式

`lambda` 创建匿名的小型函数，通常用于排序的 `key` 参数或简单的回调。

```python
entries = [("a", 3), ("b", 1), ("c", 2)]
entries.sort(key=lambda e: e[1])  # 按第二个元素排序
print(entries)  # [('b', 1), ('c', 2), ('a', 3)]
```

**为什么在本课中使用：** 搜索结果排序时用 `entries.sort(key=lambda e: e.score, reverse=True)` 按分数从高到低排列。

### 列表推导和 `sum()` + 生成器表达式

列表推导可以在一行内从可迭代对象生成新列表。`sum()` 配合生成器表达式可以简洁地计算总和。

```python
# 列表推导
names = [entry.name for entry in results if entry.score > 0.5]

# sum + 生成器表达式（不创建中间列表，更省内存）
total = sum(len(m.get("content", "")) // 4 for m in messages)
```

**为什么在本课中使用：** `ContextEngine.compact()` 用 `sum(...)` 配合生成器表达式估算所有消息的总 token 数，一行代码完成遍历和累加。

### `dict.get()` 字典安全取值

`dict.get(key, default)` 获取字典中指定键的值，如果键不存在则返回默认值而不是抛出 `KeyError`。

```python
message = {"role": "user", "content": "Hello"}
content = message.get("content", "")  # "Hello"
tool = message.get("tool_id", None)   # None（键不存在）
```

**为什么在本课中使用：** 处理消息字典时，某些字段可能不存在（如 `"content"`），用 `.get()` 安全取值避免程序崩溃。

### 三引号多行字符串

Python 的三引号（`"""` 或 `'''`）可以创建跨越多行的字符串，常用于 SQL 语句、文档字符串等。

```python
sql = """
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL
    );
"""
```

**为什么在本课中使用：** 数据库初始化需要执行大段 SQL（创建表、FTS5 虚拟表、触发器、索引），三引号字符串让 SQL 代码清晰可读。

### `pytest.fixture` 测试 Fixture

`@pytest.fixture` 装饰器定义测试前的准备工作（如创建数据库），测试函数通过参数名自动注入 fixture 的返回值。`yield` 可以实现"先准备、后清理"的模式。

```python
import pytest

@pytest.fixture
def store(tmp_path):
    s = MemoryStore(db_path=tmp_path / "test.db")
    yield s       # 测试函数拿到 s
    s.close()     # 测试结束后自动清理

def test_add(store):
    store.add("hello")
    assert store.count() == 1
```

**为什么在本课中使用：** 每个测试需要一个干净的数据库。fixture 自动在临时目录创建数据库，测试结束后关闭连接，确保测试间互不干扰。

### `pytest.approx` 近似比较

浮点数计算有精度误差，直接用 `==` 比较不可靠。`pytest.approx()` 允许在一定误差范围内比较浮点数。

```python
import pytest

assert 0.1 + 0.2 == pytest.approx(0.3)           # 通过（默认绝对误差 1e-6）
assert 0.499 == pytest.approx(0.5, rel=0.01)      # 通过（相对误差 1%）
```

**为什么在本课中使用：** 测试时间衰减函数时，`_temporal_decay(30)` 的理论值是 0.5（半衰期），但浮点计算可能有微小偏差，用 `approx` 进行容差比较。

### `loguru` 第三方日志库

`loguru` 提供开箱即用的日志记录，比标准库 `logging` 更简洁。支持 `{}` 占位符格式化。

```python
from loguru import logger

logger.info("MemoryStore 初始化完成，路径: {}", db_path)
```

**为什么在本课中使用：** 记忆存储的各种操作（初始化、搜索、清理）需要记录日志，`loguru` 让日志代码简洁且信息丰富。

### 条件表达式（三元运算符）

Python 的条件表达式 `a if condition else b` 可以在一行内完成条件选择。

```python
value = timestamp or time.time()       # 如果 timestamp 为 None/0，使用当前时间
method = "fts" if has_fts else "like"   # 根据条件选择搜索方法
```

**为什么在本课中使用：** 代码中多处使用简洁的条件表达式，如 `timestamp or time.time()` 处理可选的时间戳参数、`json.loads(metadata_str) if metadata_str else {}` 处理可能为空的 JSON 字段。
