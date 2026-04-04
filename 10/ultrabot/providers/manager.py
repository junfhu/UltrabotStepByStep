# ultrabot/providers/manager.py
"""提供者编排 — 故障转移、熔断器集成。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

from loguru import logger

from ultrabot.providers.base import LLMProvider, LLMResponse
from ultrabot.providers.circuit_breaker import CircuitBreaker, CircuitState
from ultrabot.providers.registry import ProviderSpec, find_by_name, find_by_keyword


@dataclass
class _ProviderEntry:
    """一个已注册的提供者及其熔断器。"""
    name: str
    provider: LLMProvider
    breaker: CircuitBreaker
    spec: ProviderSpec | None = None
    models: list[str] = field(default_factory=list)


class ProviderManager:
    """所有已配置 LLM 提供者的中央编排器。"""

    def __init__(self, config: Any) -> None:
        self._config = config
        self._entries: dict[str, _ProviderEntry] = {}
        self._model_index: dict[str, str] = {}   # 模型 -> 提供者名称
        self._register_from_config(config)

    async def chat_with_failover(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        stream: bool = False,
        on_content_delta: Callable | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """尝试主要提供者，失败时依次回退到健康的替代方案。"""
        model = model or getattr(self._config, "default_model", "gpt-4o")

        tried: set[str] = set()
        entries = self._ordered_entries(model)
        last_exc: Exception | None = None

        for entry in entries:
            if entry.name in tried:
                continue
            tried.add(entry.name)

            if not entry.breaker.can_execute:
                logger.debug("Skipping \'{}\' — breaker is {}", entry.name,
                             entry.breaker.state.value)
                continue

            try:
                if stream and on_content_delta:
                    resp = await entry.provider.chat_stream_with_retry(
                        messages=messages, tools=tools, model=model,
                        on_content_delta=on_content_delta, **kwargs,
                    )
                else:
                    resp = await entry.provider.chat_with_retry(
                        messages=messages, tools=tools, model=model, **kwargs,
                    )
                entry.breaker.record_success()    # 健康！
                return resp

            except Exception as exc:
                last_exc = exc
                entry.breaker.record_failure()    # 记录失败
                logger.warning(
                    "Provider \'{}\' failed: {}. Trying next.", entry.name, exc
                )

        raise RuntimeError(
            f"All providers exhausted for model \'{model}\'"
        ) from last_exc

    def _ordered_entries(self, model: str) -> list[_ProviderEntry]:
        """返回排序后的条目：主要提供者优先，然后是关键字匹配的，最后是其余的。"""
        primary_name = self._model_index.get(model)
        result: list[_ProviderEntry] = []

        # 1. 该模型的主要提供者。
        if primary_name and primary_name in self._entries:
            result.append(self._entries[primary_name])

        # 2. 关键字匹配的提供者。
        for entry in self._entries.values():
            if entry.name == primary_name:
                continue
            if entry.spec:
                for kw in entry.spec.keywords:
                    if kw in model.lower():
                        result.append(entry)
                        break

        # 3. 其余所有提供者。
        for entry in self._entries.values():
            if entry not in result:
                result.append(entry)

        return result

    def health_check(self) -> dict[str, bool]:
        """提供者健康状态（熔断器状态）的快照。"""
        return {name: e.breaker.can_execute for name, e in self._entries.items()}
