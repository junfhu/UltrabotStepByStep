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
