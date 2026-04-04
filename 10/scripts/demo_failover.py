# /// script
# requires-python = ">=3.12"
# dependencies = ["loguru", "pydantic", "pydantic-settings", "openai"]
# ///
"""
故障转移演示脚本 — 模拟提供者宕机 + 自动故障转移。

用法:
    cd 10/
    uv run scripts/demo_failover.py

无需真实 API key，所有 LLM 调用均被 mock 替换。
脚本演示以下场景:

  场景 1  主提供者正常 → 请求直接走主提供者
  场景 2  主提供者连续失败 → 熔断器跳闸 → 自动切换到备用提供者
  场景 3  恢复超时过后 → 半开探测成功 → 熔断器闭合 → 主提供者恢复服务
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

# 让 import ultrabot 能找到 src
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from ultrabot.config.schema import Config
from ultrabot.providers.base import LLMResponse
from ultrabot.providers.circuit_breaker import CircuitBreaker
from ultrabot.providers.manager import ProviderManager

# ── 日志配置 ──────────────────────────────────────────────────

logger.remove()
logger.add(
    sys.stderr,
    format=(
        "<green>{time:HH:mm:ss.SSS}</green> | "
        "<level>{level:<8}</level> | "
        "<cyan>{message}</cyan>"
    ),
    level="DEBUG",
)


# ── 构造两个提供者的配置 ─────────────────────────────────────

def make_config() -> Config:
    """创建带有主（volcengine）+ 备（openrouter）的配置。"""
    return Config(
        providers={
            "volcengine": {
                "api_key": "fake-volc-key",
                "api_base": "https://ark.cn-beijing.volces.com/api/v3",
                "priority": 1,
                "models": ["doubao-pro-256k"],
            },
            "openrouter": {
                "api_key": "fake-or-key",
                "api_base": "https://openrouter.ai/api/v1",
                "priority": 10,
                "models": ["doubao-pro-256k", "gpt-4o"],
            },
        }
    )


# ── 可控的 mock chat ─────────────────────────────────────────

class ControllableProvider:
    """在真实 provider 上包一层，可以随时 开/关 来模拟宕机。"""

    def __init__(self, name: str, healthy: bool = True) -> None:
        self.name = name
        self.healthy = healthy

    async def chat(self, **kwargs) -> LLMResponse:
        if not self.healthy:
            raise ConnectionError(f"[{self.name}] Connection refused — 模拟宕机")
        return LLMResponse(
            content=f"[{self.name}] 你好！这是来自 {self.name} 的回复。",
            finish_reason="stop",
        )


# ── 辅助：打印健康状态 ───────────────────────────────────────

def print_health(mgr: ProviderManager) -> None:
    health = mgr.health_check()
    states = {
        name: mgr._entries[name].breaker.state.value
        for name in health
    }
    logger.info("健康状态: {}", states)


# ── 主演示 ────────────────────────────────────────────────────

async def main() -> None:
    cfg = make_config()
    mgr = ProviderManager(cfg)

    # 把熔断器阈值调低（方便演示），恢复超时设为 2 秒
    for entry in mgr._entries.values():
        entry.breaker.failure_threshold = 3
        entry.breaker.recovery_timeout = 2.0

    # 用 ControllableProvider 替换真实的 chat() 方法
    ctrl_volc = ControllableProvider("volcengine", healthy=True)
    ctrl_or   = ControllableProvider("openrouter", healthy=True)
    mgr._entries["volcengine"].provider.chat = ctrl_volc.chat
    mgr._entries["openrouter"].provider.chat = ctrl_or.chat

    # 同时禁用 retry 层的 sleep（让演示秒过）
    for entry in mgr._entries.values():
        entry.provider._DEFAULT_DELAYS = (0.0, 0.0, 0.0)

    messages = [{"role": "user", "content": "你好"}]

    # ────────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("场景 1：主提供者（volcengine）正常")
    logger.info("=" * 60)

    for i in range(3):
        resp = await mgr.chat_with_failover(messages, model="doubao-pro-256k")
        logger.info("请求 {} → {}", i + 1, resp.content)
    print_health(mgr)

    # ────────────────────────────────────────────────────────
    logger.info("")
    logger.info("=" * 60)
    logger.info("场景 2：主提供者宕机 → 熔断器跳闸 → 自动故障转移")
    logger.info("=" * 60)

    ctrl_volc.healthy = False
    logger.warning(">>> 模拟 volcengine 宕机!")

    for i in range(5):
        try:
            resp = await mgr.chat_with_failover(messages, model="doubao-pro-256k")
            logger.info("请求 {} → {}", i + 1, resp.content)
        except RuntimeError as exc:
            logger.error("请求 {} → 全部提供者耗尽: {}", i + 1, exc)
        print_health(mgr)

    # ────────────────────────────────────────────────────────
    logger.info("")
    logger.info("=" * 60)
    logger.info("场景 3：等待恢复超时 → 半开探测 → 主提供者恢复")
    logger.info("=" * 60)

    ctrl_volc.healthy = True
    logger.info(">>> volcengine 已恢复! 等待 2 秒恢复超时...")
    await asyncio.sleep(2.1)

    for i in range(3):
        resp = await mgr.chat_with_failover(messages, model="doubao-pro-256k")
        logger.info("请求 {} → {}", i + 1, resp.content)
    print_health(mgr)

    # ────────────────────────────────────────────────────────
    logger.info("")
    logger.info("=" * 60)
    logger.info("演示完成!")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
