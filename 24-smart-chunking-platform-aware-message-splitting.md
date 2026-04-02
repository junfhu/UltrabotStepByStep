# 课程 24：智能分块 — 平台感知的消息拆分

**目标：** 构建一个分块器，将较长的机器人回复拆分为各平台安全的片段，同时不破坏代码块和句子完整性。

**你将学到：**
- 为什么每个聊天平台的消息长度上限各不相同
- 两种拆分策略：基于长度 和 基于段落
- 如何在拆分过程中检测并保护 Markdown 代码围栏
- 将分块功能接入出站通道路径

**新建文件：**
- `ultrabot/chunking/__init__.py` — 公共导出
- `ultrabot/chunking/chunker.py` — `ChunkMode`、`chunk_text()`、平台限制表

### 步骤 1：定义平台限制与分块模式

每个消息平台在达到特定字符数后会截断或拒绝消息。我们维护一个查找表，使分块器在消息流经 Telegram、Discord、Slack 或其他通道时能自动适配。

```python
# ultrabot/chunking/chunker.py
"""按通道对出站消息进行分块。"""

from __future__ import annotations

from enum import Enum


class ChunkMode(str, Enum):
    """拆分策略。"""
    LENGTH = "length"        # 按字符限制拆分，优先在空白处断开
    PARAGRAPH = "paragraph"  # 按空行边界拆分


# ── 平台上限（字符数） ──────────────────────────────────
# 每个通道驱动可以覆盖这些值，但以下是合理的默认值。
CHANNEL_CHUNK_LIMITS: dict[str, int] = {
    "telegram": 4096,
    "discord":  2000,
    "slack":    4000,
    "feishu":   30000,
    "qq":       4500,
    "wecom":    2048,
    "weixin":   2048,
    "webui":    0,          # 0 = 无限制（Web UI 会完整流式传输响应）
}

DEFAULT_CHUNK_LIMIT = 4000
DEFAULT_CHUNK_MODE = ChunkMode.LENGTH


def get_chunk_limit(channel: str, override: int | None = None) -> int:
    """返回 *channel* 的分块限制。0 表示无限制。"""
    if override is not None and override > 0:
        return override
    return CHANNEL_CHUNK_LIMITS.get(channel, DEFAULT_CHUNK_LIMIT)
```

**关键设计决策：**
- `0` 表示"无限制" — Web UI 直接流式传输到浏览器，因此不需要拆分。
- `override` 参数允许按通道配置覆盖默认值。

### 步骤 2：主入口 `chunk_text()`

调度器检查快速退出条件（空文本、在限制范围内），然后委托给相应的策略。

```python
def chunk_text(
    text: str,
    limit: int,
    mode: ChunkMode = ChunkMode.LENGTH,
) -> list[str]:
    """将 *text* 拆分为遵守 *limit* 的分块。

    - limit <= 0 → 将完整文本作为一个分块返回（不拆分）。
    - LENGTH 模式 → 优先在换行/空白处断开，感知代码围栏。
    - PARAGRAPH 模式 → 在空行处拆分，对过大的段落回退到 LENGTH 模式。
    """
    if not text:
        return []
    if limit <= 0:
        return [text]
    if len(text) <= limit:
        return [text]

    if mode == ChunkMode.PARAGRAPH:
        return _chunk_by_paragraph(text, limit)
    return _chunk_by_length(text, limit)
```

### 步骤 3：基于长度的拆分与代码围栏保护

棘手的部分：我们绝不能在 `` ``` `` 代码块内部拆分。如果拆分点落在未闭合的围栏内，我们会将分块扩展到包含闭合围栏。

```python
def _chunk_by_length(text: str, limit: int) -> list[str]:
    """按 *limit* 拆分，优先在换行/空白边界处断开。
    
    Markdown 围栏感知：不会在 ``` 代码块内部拆分。
    """
    chunks: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break

        candidate = remaining[:limit]

        # ── 代码围栏保护 ───────────────────────────
        # 统计开启/关闭围栏的数量。奇数表示我们在代码块内部。
        fence_count = candidate.count("```")
        if fence_count % 2 == 1:
            # 找到最后一个开启围栏之后的关闭围栏
            fence_end = remaining.find("```", candidate.rfind("```") + 3)
            if fence_end != -1 and fence_end + 3 <= len(remaining):
                split_at = fence_end + 3
                # 对齐到关闭围栏之后的下一个换行
                nl = remaining.find("\n", split_at)
                if nl != -1 and nl < split_at + 10:
                    split_at = nl + 1
                chunks.append(remaining[:split_at])
                remaining = remaining[split_at:]
                continue

        # ── 寻找最佳断开点 ───────────────────────
        # 优先级：双换行 > 单换行 > 空格
        best = -1
        for sep in ["\n\n", "\n", " "]:
            pos = candidate.rfind(sep)
            if pos > limit // 4:          # 不要断得太早
                best = pos + len(sep)
                break

        if best > 0:
            chunks.append(remaining[:best].rstrip())
            remaining = remaining[best:].lstrip()
        else:
            # 没有合适的断开点 — 硬拆分
            chunks.append(remaining[:limit])
            remaining = remaining[limit:]

    return [c for c in chunks if c.strip()]
```

### 步骤 4：基于段落的拆分

对于像 Telegram 这样能渲染 Markdown 的平台，按段落边界拆分能产生最干净的视觉效果。

```python
def _chunk_by_paragraph(text: str, limit: int) -> list[str]:
    """按段落边界（空行）拆分。
    
    对于过大的段落，回退到基于长度的拆分。
    """
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        # 单个段落超过限制 → 回退到基于长度的拆分
        if len(para) > limit:
            if current:
                chunks.append(current.rstrip())
                current = ""
            chunks.extend(_chunk_by_length(para, limit))
            continue

        # 尝试追加到当前分块
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                chunks.append(current.rstrip())
            current = para

    if current:
        chunks.append(current.rstrip())

    return [c for c in chunks if c.strip()]
```

### 步骤 5：包初始化

```python
# ultrabot/chunking/__init__.py
"""按通道对出站消息进行分块。"""

from ultrabot.chunking.chunker import (
    CHANNEL_CHUNK_LIMITS,
    DEFAULT_CHUNK_LIMIT,
    DEFAULT_CHUNK_MODE,
    ChunkMode,
    chunk_text,
    get_chunk_limit,
)

__all__ = [
    "CHANNEL_CHUNK_LIMITS",
    "DEFAULT_CHUNK_LIMIT",
    "DEFAULT_CHUNK_MODE",
    "ChunkMode",
    "chunk_text",
    "get_chunk_limit",
]
```

### 测试

```python
# tests/test_chunking.py
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
```

### 检查点

```bash
python -m pytest tests/test_chunking.py -v
```

预期结果：所有测试通过。验证代码围栏保持完整：

```python
from ultrabot.chunking import chunk_text
text = "Here:\n```\n" + "line\n" * 500 + "```\nDone."
chunks = chunk_text(text, 200)
for c in chunks:
    count = c.count("```")
    assert count % 2 == 0 or count == 0, f"分块中代码围栏被破坏！"
print(f"✓ {len(chunks)} 个分块，所有围栏完好")
```

### 本课成果

一个平台感知的消息拆分器，支持两种策略（长度和段落）、代码围栏保护以及按通道的限制表。通道在发送前调用 `chunk_text(response, get_chunk_limit("telegram"))`，用户将永远不会看到被破坏的代码块。

---
