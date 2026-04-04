# Ultrabot：30 课程开发指南
**从零开始构建一个生产级 AI 助手框架。**
本指南将带你从"向 LLM 问好"一步步走到一个完整的多提供者、多通道 AI 智能体，具备工具调用、记忆、安全防护和 Web 界面。每节课程都建立在上一节课的基础之上。每节课都包含可运行的代码和测试。  
本教程的主要思路来自于
- Nanobot (https://github.com/HKUDS/nanobot)
- Learn-Claude-Code (https://github.com/shareAI-lab/learn-claude-code/)

本课程设计由AI辅助下完成，因为课程自身也在不停修正，请参考 https://github.com/junfhu/UltrabotStepByStep，如果您觉得对您有帮助，请帮助点亮一颗星。  
本课程中使用的大模型提供商是火山引擎Code Plan，如果正好你也需要，可以使用我的邀请码获取9折优惠 https://volcengine.com/L/_01BJCkKdMc/  邀请码：HHCDB4J4）  



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

## 本课使用的 Python 知识

### `from __future__ import annotations`

这是一个特殊的导入语句，让 Python 把所有类型注解当作字符串处理（延迟求值），使新式类型语法在较早版本的 Python 中可用。

```python
from __future__ import annotations

def chunk_text(text: str, limit: int) -> list[str]:
    ...
```

**为什么在本课中使用：** 代码中使用了 `list[str]`、`dict[str, int]`、`int | None` 等内置泛型类型注解，加上这一行确保兼容 Python 3.9+。

### `Enum` 枚举类型与 `str, Enum` 多重继承

`Enum` 用于定义一组命名常量。同时继承 `str` 和 `Enum` 后，枚举值可以直接当字符串比较和使用。

```python
from enum import Enum

class ChunkMode(str, Enum):
    LENGTH = "length"
    PARAGRAPH = "paragraph"

print(ChunkMode.LENGTH == "length")  # True
print(ChunkMode.LENGTH.value)        # "length"
```

**为什么在本课中使用：** `ChunkMode` 定义了两种拆分策略（`LENGTH` 和 `PARAGRAPH`），用枚举可以防止传入无效的模式字符串，又因为继承了 `str`，可以方便地序列化和比较。

### `dict[str, int]` 类型注解的字典

Python 3.9+ 允许直接在内置类型上使用泛型语法（`dict[str, int]`）来标注字典的键值类型。

```python
CHANNEL_CHUNK_LIMITS: dict[str, int] = {
    "telegram": 4096,
    "discord": 2000,
    "slack": 4000,
}
```

**为什么在本课中使用：** 平台限制查找表是一个从通道名（字符串）到字符数限制（整数）的映射，用 `dict[str, int]` 清晰表达了数据结构。

### `list[str]` 类型注解的列表

与字典类似，`list[str]` 标注一个元素全为字符串的列表。

```python
def chunk_text(text: str, limit: int) -> list[str]:
    chunks: list[str] = []
    ...
    return chunks
```

**为什么在本课中使用：** `chunk_text()` 返回拆分后的文本列表，用 `list[str]` 清晰标注返回值类型，帮助 IDE 和类型检查器提供更好的提示。

### 字符串方法：`split()`、`strip()`、`count()`、`find()`、`rfind()`

Python 字符串提供了丰富的内置方法，用于拆分、清理和搜索：

```python
text = "Hello\n\nWorld\n\nPython"

# split() — 按分隔符拆分
paragraphs = text.split("\n\n")  # ["Hello", "World", "Python"]

# strip() / rstrip() / lstrip() — 去除首尾空白
"  hello  ".strip()   # "hello"
"hello  ".rstrip()    # "hello"

# count() — 统计子串出现次数
"```code```more```".count("```")  # 3

# find() / rfind() — 查找子串位置（rfind 从右向左查）
text.find("World")     # 7（从左找）
text.rfind("World")    # 7（从右找）
```

**为什么在本课中使用：** 分块器需要在合适的位置断开文本——`split("\n\n")` 按段落拆分，`rfind("\n")` 找到最后一个换行处断开，`count("```")` 统计代码围栏数量判断是否在代码块内部。

### `while` 循环

`while` 循环在条件为真时反复执行，适合不知道具体迭代次数的场景。

```python
remaining = "very long text..."
chunks = []
while remaining:
    if len(remaining) <= limit:
        chunks.append(remaining)
        break
    chunk = remaining[:limit]
    chunks.append(chunk)
    remaining = remaining[limit:]
```

**为什么在本课中使用：** 基于长度的拆分算法需要不断从剩余文本中切出符合限制的分块，直到没有剩余文本——这正是 `while` 循环的典型应用。

### `for` 循环与 `break` / `continue`

`for` 遍历可迭代对象。`break` 立即退出循环，`continue` 跳过本次迭代进入下一轮。

```python
for sep in ["\n\n", "\n", " "]:
    pos = candidate.rfind(sep)
    if pos > limit // 4:
        best = pos + len(sep)
        break  # 找到最佳断开点，退出循环
```

**为什么在本课中使用：** 寻找最佳断开点时，按优先级依次尝试双换行、单换行、空格——一旦找到合适的位置就用 `break` 退出，不再尝试更低优先级的分隔符。

### 列表推导与条件过滤

列表推导可以在一行内从可迭代对象生成新列表，`if` 子句可以过滤不符合条件的元素。

```python
# 过滤掉空白分块
chunks = [c for c in chunks if c.strip()]
```

**为什么在本课中使用：** 拆分后可能产生空的分块（全是空白字符），用列表推导加条件过滤一步清理干净。

### 字符串切片

Python 的切片语法 `s[start:end]` 可以从字符串中取出子串。支持省略 `start`（从头开始）或 `end`（到末尾）。

```python
text = "Hello, World!"
print(text[:5])    # "Hello"    — 前 5 个字符
print(text[7:])    # "World!"   — 第 7 个字符到末尾
print(text[-6:])   # "World!"   — 倒数 6 个字符到末尾
```

**为什么在本课中使用：** 分块的核心操作就是切片——`remaining[:limit]` 取出一个分块，`remaining[best:]` 保留剩余文本。

### 函数默认参数

函数定义时可以为参数设置默认值，调用时如果不传该参数就使用默认值。

```python
def chunk_text(
    text: str,
    limit: int,
    mode: ChunkMode = ChunkMode.LENGTH,  # 默认使用长度模式
) -> list[str]:
    ...
```

**为什么在本课中使用：** `chunk_text()` 的 `mode` 参数默认为 `LENGTH`，大多数调用者不需要关心拆分模式，简化了接口。

### `__all__` 模块导出控制

`__all__` 是一个字符串列表，定义了使用 `from module import *` 时导出哪些名称。它就像模块的"公开 API 清单"。

```python
# ultrabot/chunking/__init__.py
__all__ = [
    "ChunkMode",
    "chunk_text",
    "get_chunk_limit",
    "CHANNEL_CHUNK_LIMITS",
]
```

**为什么在本课中使用：** 明确声明 chunking 包的公开接口，隐藏内部实现函数（如 `_chunk_by_length`、`_chunk_by_paragraph`），让使用者只看到需要用的部分。

### 策略模式（函数调度）

根据条件选择不同的处理函数执行——这是"策略模式"的简单实现。在 Python 中用 `if/elif` 调度即可，无需复杂的类继承。

```python
def chunk_text(text: str, limit: int, mode: ChunkMode = ChunkMode.LENGTH) -> list[str]:
    if mode == ChunkMode.PARAGRAPH:
        return _chunk_by_paragraph(text, limit)  # 段落策略
    return _chunk_by_length(text, limit)          # 长度策略
```

**为什么在本课中使用：** 分块器提供两种策略——基于长度和基于段落。`chunk_text()` 根据 `mode` 参数调度到不同的内部函数，符合"开放-封闭"原则：添加新策略只需增加新函数和一个 `elif` 分支。

### `f-string` 格式化字符串

f-string（`f"...{expr}..."`）可以在字符串中直接嵌入变量或表达式，简洁高效。

```python
channel = "telegram"
limit = 4096
print(f"通道 {channel} 的消息限制是 {limit} 字符")
```

**为什么在本课中使用：** 测试代码中用 f-string 格式化输出信息（如 `f"✓ {len(chunks)} 个分块"`），使调试信息更清晰可读。

### `pytest` 测试框架

`pytest` 是 Python 最流行的测试框架，支持类组织测试、丰富的断言、参数化等功能。

```python
import pytest

class TestChunkText:
    def test_empty_text(self):
        assert chunk_text("", 100) == []

    def test_within_limit_returns_single(self):
        assert chunk_text("hello", 100) == ["hello"]
```

**为什么在本课中使用：** 分块逻辑有很多边界情况（空文本、在限制内、代码围栏、段落拆分），需要全面的测试来确保每种情况都正确处理。
