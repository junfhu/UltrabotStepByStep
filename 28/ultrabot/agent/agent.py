# ultrabot/agent/agent.py
"""ultrabot 核心智能体 -- 支持工具调用循环的 LLM 对话代理。"""
from __future__ import annotations

import json
from typing import Any, Callable

from loguru import logger

from ultrabot.tools.base import ToolRegistry


SYSTEM_PROMPT = """\
You are **UltraBot**, a helpful personal AI assistant.
- Answer concisely and accurately.
- When unsure, say so rather than guessing.
- Use code blocks for any code in your responses.
- Use the tools available to you when the task requires file operations,
  running commands, or web searches. Prefer tool use over speculation.
"""


class Agent:
    """支持工具调用的智能体。

    run() 方法实现了完整的工具循环。
    """

    def __init__(
        self,
        config: Any = None,
        provider_manager: Any = None,
        session_manager: Any = None,
        tool_registry: ToolRegistry | None = None,
        security_guard: Any = None,
    ) -> None:
        self._config = config
        self._provider = provider_manager
        self._sessions = session_manager
        self._tools = tool_registry or ToolRegistry()
        self._security_guard = security_guard
        self._max_iterations = getattr(config, "max_tool_iterations", 10)

    async def run(
        self,
        user_message: str,
        session_key: str = "default",
        on_content_delta: Callable[..., Any] | None = None,
        on_tool_hint: Callable[..., Any] | None = None,
    ) -> str:
        """处理一条用户消息并返回助手回复。"""
        session = await self._sessions.get_or_create(session_key)
        session.add_message({"role": "user", "content": user_message})

        tool_defs = self._tools.get_definitions() if len(self._tools) > 0 else None

        for iteration in range(self._max_iterations):
            messages = session.get_messages()

            response = await self._provider.chat_stream_with_retry(
                messages=messages,
                tools=tool_defs,
                on_content_delta=on_content_delta,
            )

            content = response.get("content") or ""
            tool_calls = response.get("tool_calls") or []

            if not tool_calls:
                session.add_message({"role": "assistant", "content": content})
                return content

            session.add_message({
                "role": "assistant",
                "content": content or None,
                "tool_calls": tool_calls,
            })

            for tc in tool_calls:
                fn_name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"]["arguments"])
                except (json.JSONDecodeError, KeyError):
                    args = {}

                if on_tool_hint:
                    await on_tool_hint(fn_name, tc["id"])

                tool = self._tools.get(fn_name)
                if tool is None:
                    result = f"Error: unknown tool '{fn_name}'"
                else:
                    try:
                        result = await tool.execute(args)
                    except Exception as exc:
                        result = f"Error executing {fn_name}: {exc}"

                session.add_message({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                })

        return content
