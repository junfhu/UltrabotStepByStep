# ultrabot/agent/title_generator.py
"""为聊天会话自动生成标题。"""
from __future__ import annotations

from typing import Any


async def generate_title(messages: list[dict[str, Any]], provider: Any = None) -> str:
    """根据对话消息生成简短标题。

    如果提供了 provider，则调用 LLM 生成标题；否则回退到截取首条用户消息。
    """
    for msg in messages:
        if msg.get("role") == "user":
            content = msg.get("content", "")
            return content[:50].strip() or "New Chat"
    return "New Chat"
