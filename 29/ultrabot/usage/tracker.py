# ultrabot/usage/tracker.py  （关键摘录 — 完整文件约 310 行）
"""LLM API 调用的用量和成本追踪。"""

from __future__ import annotations
import json, time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any
from loguru import logger

# ── 定价表（美元/百万 token） ──────────────────────────
PRICING: dict[str, dict[str, dict[str, float]]] = {
    "anthropic": {
        "claude-sonnet-4-20250514": {"input": 3.0, "output": 15.0,
                                       "cache_read": 0.3, "cache_write": 3.75},
        "claude-opus-4-20250514": {"input": 15.0, "output": 75.0,
                                     "cache_read": 1.5, "cache_write": 18.75},
        "claude-3-5-haiku-20241022": {"input": 0.8, "output": 4.0,
                                       "cache_read": 0.08, "cache_write": 1.0},
    },
    "openai": {
        "gpt-4o": {"input": 2.5, "output": 10.0},
        "gpt-4o-mini": {"input": 0.15, "output": 0.6},
    },
    "deepseek": {
        "deepseek-chat": {"input": 0.14, "output": 0.28, "cache_read": 0.014},
    },
}


@dataclass
class UsageRecord:
    """单次 API 调用的用量记录。"""
    timestamp: float = field(default_factory=time.time)
    provider: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    session_key: str = ""
    tool_calls: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, data: dict) -> UsageRecord:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def calculate_cost(provider: str, model: str, input_tokens: int = 0,
                   output_tokens: int = 0, **kwargs) -> float:
    """根据给定用量计算美元成本。"""
    provider_pricing = PRICING.get(provider, {})
    model_pricing = provider_pricing.get(model)
    if model_pricing is None:
        # 尝试前缀匹配
        for known, pricing in provider_pricing.items():
            if known in model.lower() or model.lower() in known:
                model_pricing = pricing
                break
    if model_pricing is None:
        return 0.0
    cost = input_tokens * model_pricing.get("input", 0) / 1_000_000
    cost += output_tokens * model_pricing.get("output", 0) / 1_000_000
    cost += kwargs.get("cache_read_tokens", 0) * model_pricing.get("cache_read", 0) / 1_000_000
    cost += kwargs.get("cache_write_tokens", 0) * model_pricing.get("cache_write", 0) / 1_000_000
    return cost


class UsageTracker:
    """追踪并持久化 LLM API 用量和成本。"""

    def __init__(self, data_dir: Path | None = None, max_records: int = 10000):
        self._data_dir = data_dir
        self._max_records = max_records
        self._records: list[UsageRecord] = []
        self._total_tokens = 0
        self._total_cost = 0.0
        self._by_model: dict[str, dict[str, float]] = defaultdict(
            lambda: {"tokens": 0, "cost": 0.0})
        self._daily: dict[str, dict[str, float]] = defaultdict(
            lambda: {"tokens": 0, "cost": 0.0, "calls": 0})

    def record(self, provider: str, model: str, raw_usage: dict,
               session_key: str = "", tool_names: list[str] | None = None) -> UsageRecord:
        """记录单次 API 调用的用量。"""
        cost = calculate_cost(provider, model,
                              raw_usage.get("input_tokens", 0),
                              raw_usage.get("output_tokens", 0))
        rec = UsageRecord(provider=provider, model=model, cost_usd=cost,
                          input_tokens=raw_usage.get("input_tokens", 0),
                          output_tokens=raw_usage.get("output_tokens", 0),
                          total_tokens=raw_usage.get("total_tokens", 0),
                          session_key=session_key, tool_calls=tool_names or [])
        self._records.append(rec)
        self._total_tokens += rec.total_tokens
        self._total_cost += rec.cost_usd
        today = date.today().isoformat()
        self._daily[today]["tokens"] += rec.total_tokens
        self._daily[today]["cost"] += rec.cost_usd
        self._daily[today]["calls"] += 1
        while len(self._records) > self._max_records:
            self._records.pop(0)
        return rec

    def get_summary(self) -> dict[str, Any]:
        return {"total_tokens": self._total_tokens,
                "total_cost_usd": round(self._total_cost, 6),
                "total_calls": len(self._records),
                "daily": dict(self._daily)}
