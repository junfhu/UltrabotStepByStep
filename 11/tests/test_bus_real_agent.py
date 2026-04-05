# tests/test_bus_real_agent.py
"""端到端真实测试：入站消息 → MessageBus → Agent(LLM) → 出站回复。

凭据解析优先级：
  1. 环境变量 OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL
  2. ~/.ultrabot/config.json 中的默认提供者配置

运行：
  pytest tests/test_bus_real_agent.py -v -s
"""
import asyncio
import os

import pytest
from openai import OpenAI

from ultrabot.agent import Agent
from ultrabot.bus.events import InboundMessage, OutboundMessage
from ultrabot.bus.queue import MessageBus


# ── 凭据解析：环境变量优先，否则读取 config.json ──

def _resolve_credentials() -> tuple[str | None, str | None, str | None]:
    """返回 (api_key, base_url, model)，找不到则返回 None。"""
    api_key = os.environ.get("OPENAI_API_KEY")
    base_url = os.environ.get("OPENAI_BASE_URL")
    model = os.environ.get("OPENAI_MODEL")

    if api_key and base_url:
        return api_key, base_url, model or "gpt-4o-mini"

    # 回退：从 ~/.ultrabot/config.json 读取
    try:
        from ultrabot.config import load_config
        cfg = load_config()
        provider_name = cfg.agents.defaults.provider
        prov = cfg.providers.all_providers().get(provider_name)
        if prov and prov.api_key and prov.enabled:
            return (
                api_key or prov.api_key,
                base_url or prov.api_base,
                model or cfg.agents.defaults.model,
            )
    except Exception:
        pass

    return api_key, base_url, model


_api_key, _base_url, _model = _resolve_credentials()

_skip = pytest.mark.skipif(
    not _api_key or not _base_url,
    reason="No LLM credentials: set OPENAI_API_KEY + OPENAI_BASE_URL, or configure ~/.ultrabot/config.json",
)


def _make_agent() -> Agent:
    """构建一个连接到真实 LLM 的 Agent。"""
    client = OpenAI(api_key=_api_key, base_url=_base_url)
    return Agent(client=client, model=_model)


@_skip
def test_bus_with_real_agent():
    """完整链路：用户消息 → 总线 → Agent → LLM → 出站回复。"""
    agent = _make_agent()

    async def _run():
        bus = MessageBus()
        delivered: list[OutboundMessage] = []

        async def agent_handler(msg: InboundMessage) -> OutboundMessage:
            reply = await agent.run(msg.content, session_key=msg.session_key)
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=reply,
            )

        async def channel_sender(msg: OutboundMessage):
            delivered.append(msg)

        bus.set_inbound_handler(agent_handler)
        bus.subscribe(channel_sender)

        await bus.publish(InboundMessage(
            channel="telegram", sender_id="u1",
            chat_id="c1", content="请用一句话介绍你自己",
        ))

        task = asyncio.create_task(bus.dispatch_inbound())
        await asyncio.sleep(30)
        bus.shutdown()
        await task

        assert len(delivered) == 1
        assert len(delivered[0].content) > 0
        print(f"\n[Agent replied] {delivered[0].content}")

    asyncio.run(_run())


@_skip
def test_bus_multi_turn_conversation():
    """多轮对话：验证总线能串行处理多条消息并保持会话。"""
    agent = _make_agent()

    async def _run():
        bus = MessageBus()
        delivered: list[OutboundMessage] = []

        async def agent_handler(msg: InboundMessage) -> OutboundMessage:
            reply = await agent.run(msg.content, session_key=msg.session_key)
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=reply,
            )

        async def channel_sender(msg: OutboundMessage):
            delivered.append(msg)

        bus.set_inbound_handler(agent_handler)
        bus.subscribe(channel_sender)

        # 第一轮
        await bus.publish(InboundMessage(
            channel="telegram", sender_id="u1",
            chat_id="c1", content="我的名字叫小明，请记住",
        ))

        task = asyncio.create_task(bus.dispatch_inbound())
        await asyncio.sleep(30)

        # 第二轮 — 测试 Agent 是否记住了上下文
        await bus.publish(InboundMessage(
            channel="telegram", sender_id="u1",
            chat_id="c1", content="我叫什么名字？",
        ))
        await asyncio.sleep(30)

        bus.shutdown()
        await task

        assert len(delivered) == 2
        print(f"\n[Round 1] {delivered[0].content}")
        print(f"[Round 2] {delivered[1].content}")
        assert "小明" in delivered[1].content

    asyncio.run(_run())
