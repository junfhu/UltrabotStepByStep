"""智能分块系统的测试。"""

import pytest
from ultrabot.chunking.chunker import (
    ChunkMode, chunk_text, get_chunk_limit,
    CHANNEL_CHUNK_LIMITS,
)


class TestGetChunkLimit:
    def test_known_channel(self):
        assert get_chunk_limit("telegram") == 4096
        assert get_chunk_limit("discord") == 2000

    def test_unknown_channel_returns_default(self):
        assert get_chunk_limit("matrix") == 4000

    def test_override_wins(self):
        assert get_chunk_limit("telegram", override=1000) == 1000

    def test_zero_override_uses_channel_default(self):
        assert get_chunk_limit("discord", override=0) == 2000

    def test_webui_unlimited(self):
        assert get_chunk_limit("webui") == 0


class TestChunkText:
    def test_empty_text(self):
        assert chunk_text("", 100) == []

    def test_within_limit_returns_single(self):
        assert chunk_text("hello", 100) == ["hello"]

    def test_unlimited_returns_single(self):
        big = "x" * 10_000
        assert chunk_text(big, 0) == [big]

    def test_splits_at_whitespace(self):
        text = "word " * 100  # 500 字符
        chunks = chunk_text(text.strip(), 120)
        assert len(chunks) >= 2
        for chunk in chunks:
            assert len(chunk) <= 140  # rstrip 后有一些余量

    def test_code_fence_protection(self):
        """代码块绝不应该在中间被拆分。"""
        text = "Before\n```python\n" + "x = 1\n" * 50 + "```\nAfter"
        chunks = chunk_text(text, 100)
        # 找到包含代码围栏开始的分块
        for chunk in chunks:
            if "```python" in chunk:
                # 必须同时包含闭合围栏
                assert "```" in chunk[chunk.index("```python") + 3:]
                break

    def test_paragraph_mode_splits_at_blank_lines(self):
        text = "Para one.\n\nPara two.\n\nPara three."
        chunks = chunk_text(text, 20, mode=ChunkMode.PARAGRAPH)
        assert len(chunks) >= 2

    def test_paragraph_mode_oversized_falls_back(self):
        text = "Short.\n\n" + "x" * 200  # 第二个段落很大
        chunks = chunk_text(text, 50, mode=ChunkMode.PARAGRAPH)
        assert len(chunks) >= 2
        assert chunks[0] == "Short."

from ultrabot.chunking import chunk_text
text = "Here:\n```\n" + "line\n" * 500 + "```\nDone."
chunks = chunk_text(text, 200)
for c in chunks:
    count = c.count("```")
    assert count % 2 == 0 or count == 0, f"分块中代码围栏被破坏！"
print(f"✓ {len(chunks)} 个分块，所有围栏完好")
