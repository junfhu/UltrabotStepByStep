# Ultrabot：30 课程开发指南
**从零开始构建一个生产级 AI 助手框架。**
本指南将带你从"向 LLM 问好"一步步走到一个完整的多提供者、多通道 AI 智能体，具备工具调用、记忆、安全防护和 Web 界面。每节课程都建立在上一节课的基础之上。每节课都包含可运行的代码和测试。  
本教程的主要思路来自于
- Nanobot (https://github.com/HKUDS/nanobot)
- Learn-Claude-Code (https://github.com/shareAI-lab/learn-claude-code/)

本课程设计由AI辅助下完成，因为课程自身也在不停修正，请参考 https://github.com/junfhu/UltrabotStepByStep，如果您觉得对您有帮助，请帮助点亮一颗星。  
本课程中使用的大模型提供商是火山引擎Code Plan，如果正好你也需要，可以使用我的邀请码获取9折优惠 https://volcengine.com/L/_01BJCkKdMc/  邀请码：HHCDB4J4）  



# 课程 26：提示词缓存 + 辅助客户端

**目标：** 通过 Anthropic 的提示词缓存将多轮对话的 API 成本降低约 75%，并新增一个廉价的"辅助" LLM 用于元数据任务。

**你将学到：**
- Anthropic `cache_control` 断点的工作原理
- 三种缓存策略：`system_only`、`system_and_3`、`none`
- 缓存命中/未命中的统计追踪
- 一个轻量级异步 HTTP 客户端，用于廉价的 LLM 调用（摘要、标题、分类）

**新建文件：**
- `ultrabot/providers/prompt_cache.py` — `PromptCacheManager`、`CacheStats`
- `ultrabot/agent/auxiliary.py` — `AuxiliaryClient`

### 步骤 1：缓存统计追踪器

```python
# ultrabot/providers/prompt_cache.py
"""Anthropic 提示词缓存 -- system_and_3 策略。

通过缓存对话前缀，将多轮对话的输入 token 成本降低约 75%。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CacheStats:
    """提示词缓存使用的运行统计。"""
    hits: int = 0
    misses: int = 0
    total_tokens_saved: int = 0

    def record_hit(self, tokens_saved: int = 0) -> None:
        self.hits += 1
        self.total_tokens_saved += tokens_saved

    def record_miss(self) -> None:
        self.misses += 1

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0
```

### 步骤 2：PromptCacheManager

管理器将 `cache_control: {"type": "ephemeral"}` 标记注入到消息中。Anthropic 的 API 会缓存最后一个标记之前的所有内容，因此后续具有相同前缀的请求将跳过对这些 token 的重新处理。

```python
class PromptCacheManager:
    """管理 Anthropic 提示词缓存断点。

    策略
    ----------
    * "system_and_3" -- 标记系统消息 + 最后 3 条用户/助手消息。
    * "system_only"  -- 仅标记系统消息。
    * "none"         -- 原样返回消息，不做修改。
    """

    def __init__(self) -> None:
        self.stats = CacheStats()

    def apply_cache_hints(
        self,
        messages: list[dict[str, Any]],
        strategy: str = "system_and_3",
    ) -> list[dict[str, Any]]:
        """返回带有缓存控制断点的 *messages* 深拷贝。
        
        原始列表不会被修改。
        """
        if strategy == "none" or not messages:
            return copy.deepcopy(messages)

        out = copy.deepcopy(messages)
        marker: dict[str, str] = {"type": "ephemeral"}

        if strategy == "system_only":
            self._mark_system(out, marker)
            return out

        # 默认策略：system_and_3
        self._mark_system(out, marker)

        # 选取最后 3 条非系统消息设置缓存断点
        non_sys_indices = [
            i for i, m in enumerate(out) if m.get("role") != "system"
        ]
        for idx in non_sys_indices[-3:]:
            self._apply_marker(out[idx], marker)

        return out

    @staticmethod
    def is_anthropic_model(model: str) -> bool:
        """当 *model* 看起来像 Anthropic 模型名称时返回 True。"""
        return model.lower().startswith("claude")

    @staticmethod
    def _apply_marker(msg: dict[str, Any], marker: dict[str, str]) -> None:
        """将 cache_control 注入到 *msg* 中。"""
        content = msg.get("content")

        if content is None or content == "":
            msg["cache_control"] = marker
            return

        # 字符串内容 → 转换为带 cache_control 的块格式
        if isinstance(content, str):
            msg["content"] = [
                {"type": "text", "text": content, "cache_control": marker},
            ]
            return

        # 列表内容 → 标记最后一个块
        if isinstance(content, list) and content:
            last = content[-1]
            if isinstance(last, dict):
                last["cache_control"] = marker

    def _mark_system(self, messages: list[dict], marker: dict) -> None:
        """标记第一条系统消息（如果存在）。"""
        if messages and messages[0].get("role") == "system":
            self._apply_marker(messages[0], marker)
```

### 步骤 3：辅助客户端

一个用于"辅助"任务的最小化异步 HTTP 客户端 — 例如生成对话标题或分类消息。使用廉价模型（GPT-4o-mini、Gemini Flash）以将成本控制在接近零。

```python
# ultrabot/agent/auxiliary.py
"""辅助 LLM 客户端，用于辅助任务（摘要、标题生成、分类）。

基于 OpenAI 兼容聊天补全端点的轻量级异步包装器。
"""

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://api.openai.com/v1"


class AuxiliaryClient:
    """通过 OpenAI 兼容端点执行辅助 LLM 任务的异步客户端。

    Parameters
    ----------
    provider : str
        人类可读的提供商名称（如 "openai"、"openrouter"）。
    model : str
        模型标识符（如 "gpt-4o-mini"）。
    api_key : str
        API 的 Bearer token。
    base_url : str, optional
        端点的基础 URL。默认为 OpenAI。
    timeout : float, optional
        请求超时时间（秒）。默认 30。
    """

    def __init__(
        self,
        provider: str,
        model: str,
        api_key: str,
        base_url: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.base_url = (base_url or _DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        """延迟初始化底层 httpx 客户端。"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=self.timeout,
            )
        return self._client

    async def close(self) -> None:
        """关闭底层 HTTP 客户端。"""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def complete(
        self,
        messages: list[dict],
        max_tokens: int = 512,
        temperature: float = 0.3,
    ) -> str:
        """发送聊天补全请求并返回助手的文本。
        
        任何失败均返回空字符串。
        """
        if not messages:
            return ""

        client = self._get_client()
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        try:
            response = await client.post("/chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()
            choices = data.get("choices", [])
            if not choices:
                return ""
            content = choices[0].get("message", {}).get("content", "")
            return (content or "").strip()
        except Exception as exc:
            logger.debug("AuxiliaryClient.complete failed: %s", exc)
            return ""

    async def summarize(self, text: str, max_tokens: int = 256) -> str:
        """将文本摘要为简洁的一段话。"""
        if not text:
            return ""
        messages = [
            {"role": "system", "content":
             "You are a concise summarizer. Be brief."},
            {"role": "user", "content": text},
        ]
        return await self.complete(messages, max_tokens=max_tokens, temperature=0.3)

    async def generate_title(self, messages: list[dict], max_tokens: int = 32) -> str:
        """为对话生成一个简短的描述性标题。"""
        if not messages:
            return ""
        snippet_parts: list[str] = []
        for msg in messages[:4]:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if content:
                snippet_parts.append(f"{role}: {content[:200]}")
        snippet = "\n".join(snippet_parts)

        title_messages = [
            {"role": "system", "content":
             "Generate a short, descriptive title (3-7 words) for this "
             "conversation. Return ONLY the title text."},
            {"role": "user", "content": snippet},
        ]
        return await self.complete(title_messages, max_tokens=max_tokens, temperature=0.3)

    async def classify(self, text: str, categories: list[str]) -> str:
        """将文本分类到给定类别之一。"""
        if not text or not categories:
            return ""
        cats_str = ", ".join(categories)
        messages = [
            {"role": "system", "content":
             f"Classify the following text into exactly one of these "
             f"categories: {cats_str}. Respond with ONLY the category name."},
            {"role": "user", "content": text},
        ]
        result = await self.complete(messages, max_tokens=20, temperature=0.1)
        result_lower = result.strip().lower()
        for cat in categories:
            if cat.lower() == result_lower:
                return cat
        for cat in categories:
            if cat.lower() in result_lower:
                return cat
        return result
```

### 测试

```python
# tests/test_prompt_cache.py
"""提示词缓存和辅助客户端的测试。"""

import pytest
from ultrabot.providers.prompt_cache import PromptCacheManager, CacheStats


class TestCacheStats:
    def test_hit_rate_empty(self):
        stats = CacheStats()
        assert stats.hit_rate == 0.0

    def test_hit_rate(self):
        stats = CacheStats(hits=3, misses=1)
        assert stats.hit_rate == 0.75

    def test_record_hit(self):
        stats = CacheStats()
        stats.record_hit(tokens_saved=100)
        assert stats.hits == 1
        assert stats.total_tokens_saved == 100


class TestPromptCacheManager:
    def test_none_strategy_no_markers(self):
        mgr = PromptCacheManager()
        msgs = [{"role": "system", "content": "Hello"}]
        result = mgr.apply_cache_hints(msgs, strategy="none")
        assert "cache_control" not in str(result)

    def test_system_only_marks_system(self):
        mgr = PromptCacheManager()
        msgs = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Hi"},
        ]
        result = mgr.apply_cache_hints(msgs, strategy="system_only")
        # 系统消息内容转换为带 cache_control 的列表
        assert isinstance(result[0]["content"], list)
        assert result[0]["content"][0]["cache_control"]["type"] == "ephemeral"
        # 用户消息未被修改
        assert isinstance(result[1]["content"], str)

    def test_system_and_3_marks_last_three(self):
        mgr = PromptCacheManager()
        msgs = [
            {"role": "system", "content": "Sys"},
            {"role": "user", "content": "U1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "U2"},
            {"role": "assistant", "content": "A2"},
            {"role": "user", "content": "U3"},
        ]
        result = mgr.apply_cache_hints(msgs, strategy="system_and_3")
        # 系统消息已标记
        assert isinstance(result[0]["content"], list)
        # 最后 3 条非系统消息已标记（索引 3、4、5）
        for idx in [3, 4, 5]:
            assert isinstance(result[idx]["content"], list)
        # 前面的非系统消息未被标记
        assert isinstance(result[1]["content"], str)

    def test_original_not_mutated(self):
        mgr = PromptCacheManager()
        msgs = [{"role": "system", "content": "Hello"}]
        original_content = msgs[0]["content"]
        mgr.apply_cache_hints(msgs)
        assert msgs[0]["content"] == original_content  # 仍然是字符串

    def test_is_anthropic_model(self):
        assert PromptCacheManager.is_anthropic_model("claude-sonnet-4-20250514")
        assert not PromptCacheManager.is_anthropic_model("gpt-4o")
```

### 检查点

```bash
python -m pytest tests/test_prompt_cache.py -v
```

预期结果：所有测试通过。在生产日志中你会看到：
```
Cache stats: 15 hits, 3 misses (83% hit rate), ~12K tokens saved
```

### 本课成果

一个 `PromptCacheManager`，通过注入 Anthropic 缓存断点来降低约 75% 的成本；加上一个 `AuxiliaryClient`，使用低价模型执行廉价的元数据任务（标题、摘要、分类）。两者结合使 ultrabot 在规模化使用时保持低成本。

---

## 本课使用的 Python 知识

### `from __future__ import annotations`（延迟注解求值）

这是一个特殊的导入语句，让 Python 不会在定义时立即求值类型注解，而是将它们作为字符串保留。这样你就可以在类型注解中使用尚未定义的类名，或者使用 `list[dict]` 这样的现代语法而不必担心运行时报错。

```python
from __future__ import annotations

# 没有这行，在 Python 3.9 中 list[dict] 会报错
def process(items: list[dict[str, Any]]) -> list[str]:
    ...
```

**为什么在本课中使用：** `PromptCacheManager` 的方法签名中大量使用了 `list[dict[str, Any]]` 这种现代泛型语法，`from __future__ import annotations` 确保这些类型注解在所有 Python 3.x 版本中都能正常工作。

### `@dataclass` 装饰器（数据类）

`@dataclass` 是 Python 的一个装饰器，它会自动为类生成 `__init__`、`__repr__`、`__eq__` 等方法。你只需要声明字段和类型，Python 自动帮你写好构造函数。

```python
from dataclasses import dataclass

@dataclass
class Point:
    x: float = 0.0
    y: float = 0.0

p = Point(1.0, 2.0)  # 自动生成的 __init__
print(p)               # 自动生成的 __repr__: Point(x=1.0, y=2.0)
```

**为什么在本课中使用：** `CacheStats` 用 `@dataclass` 定义了 `hits`、`misses`、`total_tokens_saved` 三个字段，省去了手写 `__init__` 的模板代码，让统计数据的结构一目了然。

### `@property` 装饰器（属性访问器）

`@property` 让你可以把一个方法伪装成属性来访问。调用者使用 `obj.hit_rate` 而不是 `obj.hit_rate()`，看起来更自然。

```python
class Circle:
    def __init__(self, radius):
        self.radius = radius

    @property
    def area(self):
        return 3.14159 * self.radius ** 2

c = Circle(5)
print(c.area)  # 像属性一样访问，不用加括号
```

**为什么在本课中使用：** `CacheStats.hit_rate` 是一个计算值（命中次数除以总次数），用 `@property` 可以让调用者像读取普通属性一样获取命中率，而不需要记住这是一个方法。

### `@staticmethod` 装饰器（静态方法）

静态方法是属于类但不需要访问实例（`self`）或类（`cls`）的方法。它本质上是放在类命名空间里的普通函数。

```python
class MathHelper:
    @staticmethod
    def add(a, b):
        return a + b

MathHelper.add(3, 5)  # 不需要创建实例就能调用
```

**为什么在本课中使用：** `PromptCacheManager.is_anthropic_model()` 只需要检查模型名是否以 "claude" 开头，不需要访问任何实例状态，所以用 `@staticmethod` 明确表达了这个意图。`_apply_marker` 和 `_mark_system` 也是如此。

### `copy.deepcopy()`（深拷贝）

深拷贝会递归地复制一个对象及其所有嵌套的子对象，修改副本不会影响原始数据。与之对应的浅拷贝（`copy.copy()`）只复制顶层。

```python
import copy

original = [{"a": [1, 2]}, {"b": 3}]
deep = copy.deepcopy(original)
deep[0]["a"].append(999)
print(original[0]["a"])  # [1, 2] — 原始数据没变
```

**为什么在本课中使用：** `apply_cache_hints` 需要给消息注入缓存标记，但绝不能修改调用者传入的原始消息列表。深拷贝确保返回的是一个全新的副本，原始对话数据保持不变。

### `isinstance()` 类型检查

`isinstance()` 用于检查一个对象是否是某个类型（或类型元组中的一个）。它比 `type(x) == str` 更推荐，因为它支持继承。

```python
value = [1, 2, 3]
if isinstance(value, list):
    print("是列表")
if isinstance(value, (list, tuple)):
    print("是列表或元组")
```

**为什么在本课中使用：** `_apply_marker` 方法需要根据消息 `content` 的类型（字符串、列表、None）采取不同的处理策略。字符串内容需要转换为列表格式，列表内容只需标记最后一个元素。

### 列表推导式（List Comprehension）

列表推导式是一种简洁的语法，用于从一个序列创建新列表，可以带过滤条件。

```python
# 找出所有偶数的索引
numbers = [10, 15, 20, 25, 30]
even_indices = [i for i, n in enumerate(numbers) if n % 2 == 0]
# 结果: [0, 2, 4]
```

**为什么在本课中使用：** `non_sys_indices = [i for i, m in enumerate(out) if m.get("role") != "system"]` 用列表推导式快速找出所有非系统消息的索引位置，然后对最后 3 条设置缓存断点。

### 切片操作 `[-3:]`

Python 的切片语法 `[start:stop:step]` 用于获取序列的子集。负数索引从末尾开始计算，`[-3:]` 表示"取最后 3 个元素"。

```python
items = [1, 2, 3, 4, 5]
print(items[-3:])  # [3, 4, 5] — 最后3个
print(items[:2])   # [1, 2] — 前2个
```

**为什么在本课中使用：** `non_sys_indices[-3:]` 获取最后 3 条非系统消息的索引，这正是 `system_and_3` 缓存策略的核心 —— 只缓存最近的 3 条对话消息。

### `async/await`（异步编程）

`async def` 定义一个异步函数（协程），`await` 用于等待一个异步操作完成。异步编程让程序在等待网络请求时可以去做其他事情，而不是阻塞等待。

```python
import httpx

async def fetch(url):
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        return response.text
```

**为什么在本课中使用：** `AuxiliaryClient` 的所有方法（`complete`、`summarize`、`generate_title`、`classify`）都是异步的，因为它们需要发送 HTTP 请求到 LLM API。异步方式让 ultrabot 可以在等待 API 响应时处理其他任务。

### `httpx.AsyncClient`（异步 HTTP 客户端）

`httpx` 是一个现代的 Python HTTP 库，支持同步和异步两种模式。`AsyncClient` 是其异步版本，适合在 `async/await` 环境中使用。

```python
import httpx

client = httpx.AsyncClient(
    base_url="https://api.example.com",
    headers={"Authorization": "Bearer xxx"},
    timeout=30.0,
)
response = await client.post("/endpoint", json={"key": "value"})
data = response.json()
```

**为什么在本课中使用：** `AuxiliaryClient` 使用 `httpx.AsyncClient` 向 OpenAI 兼容端点发送聊天补全请求。它支持设置基础 URL、请求头和超时，非常适合封装 API 调用。

### 延迟初始化模式（Lazy Initialization）

延迟初始化是指在第一次需要时才创建对象，而不是在构造函数中就创建。这可以节省资源，特别是当对象可能不会被使用时。

```python
class Connection:
    def __init__(self):
        self._client = None  # 先不创建

    def _get_client(self):
        if self._client is None:  # 第一次调用时才创建
            self._client = create_expensive_client()
        return self._client
```

**为什么在本课中使用：** `AuxiliaryClient._get_client()` 延迟创建 `httpx.AsyncClient`，只在第一次真正发送请求时才初始化。如果辅助客户端从未被使用，就不会浪费资源创建 HTTP 连接。

### `try/except` 异常处理

`try/except` 用于捕获和处理运行时错误，防止程序崩溃。可以捕获特定类型的异常，也可以用 `Exception` 捕获所有异常。

```python
try:
    result = 10 / 0
except ZeroDivisionError:
    print("不能除以零")
except Exception as e:
    print(f"其他错误: {e}")
```

**为什么在本课中使用：** `AuxiliaryClient.complete()` 用 `try/except` 包裹整个 HTTP 请求过程，任何网络错误、API 错误都会被捕获并返回空字符串，确保辅助任务的失败不会影响主流程。

### `logging` 模块（日志记录）

Python 的 `logging` 模块提供了灵活的日志系统，支持不同级别（DEBUG、INFO、WARNING、ERROR）和不同输出目标。

```python
import logging

logger = logging.getLogger(__name__)
logger.debug("调试信息: %s", some_var)
logger.error("出错了: %s", error)
```

**为什么在本课中使用：** `AuxiliaryClient` 使用 `logger.debug()` 记录请求失败的详细信息，方便开发者调试问题，但不会在正常运行时输出噪音。

### `typing.Optional`（可选类型）

`Optional[X]` 等价于 `X | None`，表示一个值可以是指定类型或 `None`。

```python
from typing import Optional

def find_user(name: str) -> Optional[dict]:
    # 找到返回字典，找不到返回 None
    ...
```

**为什么在本课中使用：** `AuxiliaryClient.__init__` 中的 `base_url: Optional[str] = None` 表示基础 URL 可以不传（默认使用 OpenAI 的 URL），`_client: Optional[httpx.AsyncClient]` 表示客户端可能尚未初始化。

### `dict.get()` 安全取值

`dict.get(key, default)` 在键不存在时返回默认值而不是抛出 `KeyError`。

```python
data = {"name": "Alice"}
age = data.get("age", 0)       # 键不存在，返回默认值 0
name = data.get("name", "?")   # 键存在，返回 "Alice"
```

**为什么在本课中使用：** 解析 API 响应时，`data.get("choices", [])` 和 `choices[0].get("message", {}).get("content", "")` 通过链式 `.get()` 安全地层层提取数据，即使响应格式不完整也不会崩溃。

### `pytest` 测试框架

`pytest` 是 Python 最流行的测试框架，支持用类组织测试、断言自动对比、丰富的插件系统。

```python
import pytest

class TestCalculator:
    def test_add(self):
        assert 1 + 1 == 2

    def test_divide_by_zero(self):
        with pytest.raises(ZeroDivisionError):
            1 / 0
```

**为什么在本课中使用：** `TestCacheStats` 和 `TestPromptCacheManager` 用测试类组织了多个测试方法，验证缓存统计计算和缓存标记注入逻辑的正确性。每个测试方法都是独立的、可重复运行的。

### f-string 格式化字符串

f-string（格式化字符串字面量）用 `f"..."` 语法，允许在字符串中直接嵌入 Python 表达式。

```python
name = "World"
count = 42
print(f"Hello {name}, count={count}")
print(f"Bearer {api_key}")
```

**为什么在本课中使用：** 在构造 HTTP 请求头 `f"Bearer {self.api_key}"` 和拼接对话片段 `f"{role}: {content[:200]}"` 时，f-string 让字符串拼接变得直观简洁。
