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
