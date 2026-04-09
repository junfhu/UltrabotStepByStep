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
