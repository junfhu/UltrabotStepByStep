# ultrabot/heartbeat/service.py
"""心跳服务 -- 对 LLM 提供者进行定期健康检查。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from ultrabot.providers.manager import ProviderManager


class HeartbeatService:
    """定期 ping 已配置的 LLM 提供者并记录其健康状态。

    参数：
        config: 心跳配置（间隔、是否启用）。可为 None。
        provider_manager: 用于访问每个提供者的 ProviderManager。
    """

    def __init__(
        self,
        config: Any | None,
        provider_manager: "ProviderManager",
    ) -> None:
        self._config = config
        self._provider_manager = provider_manager
        self._task: asyncio.Task[None] | None = None
        self._running = False

        # 使用合理的默认值提取设置
        if config is not None:
            self._enabled: bool = getattr(config, "enabled", True)
            self._interval: int = getattr(config, "interval_s", 30)
        else:
            self._enabled = False
            self._interval = 30

    async def start(self) -> None:
        if not self._enabled:
            logger.debug("Heartbeat service is disabled")
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="heartbeat")
        logger.info("Heartbeat service started (interval={}s)", self._interval)

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Heartbeat service stopped")

    async def _loop(self) -> None:
        """按配置的间隔运行 _check，直到停止。"""
        while self._running:
            try:
                await self._check()
            except Exception:
                logger.exception("Heartbeat check failed")
            await asyncio.sleep(self._interval)

    async def _check(self) -> None:
        """通过熔断器健康检查检测所有提供者并记录状态。"""
        health = self._provider_manager.health_check()
        for name, healthy in health.items():
            if healthy:
                logger.debug("Heartbeat: provider '{}' healthy (circuit closed)", name)
            else:
                logger.warning("Heartbeat: provider '{}' UNHEALTHY (circuit open)", name)
