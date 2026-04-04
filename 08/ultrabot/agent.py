# ultrabot/agent.py
from __future__ import annotations
import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Callable

import os
from openai import OpenAI
from ultrabot.tools.base import ToolRegistry


@dataclass
class ToolCallRequest:
    """LLM 请求的单个工具调用。

    取自 ultrabot/agent.py 第 24-30 行。
    """
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

@dataclass
class LLMResponse:
    """来自任何 LLM 提供者的标准化响应。"""
    content: str | None = None
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


# -- 智能体 --

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

    对应 ultrabot/agent.py -- run() 方法实现了
    完整的工具循环。
    """

    def __init__(
        self,
        client: OpenAI,
        model: str,
        system_prompt: str = SYSTEM_PROMPT,
        max_iterations: int = 10,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._system_prompt = system_prompt
        self._max_iterations = max_iterations
        self._tools = tool_registry or ToolRegistry()
        self._messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt}
        ]

    def run(
        self,
        user_message: str,
        on_content_delta: Callable[[str], None] | None = None,
    ) -> str:
        """通过完整的智能体循环处理用户消息。

        循环（取自 ultrabot/agent.py 第 110-174 行）：
        1. 调用 LLM
        2. 如果返回 tool_calls -> 执行它们 -> 追加结果 -> 继续循环
        3. 如果只返回文本  -> 这就是最终答案 -> 跳出循环
        """
        # 1. 追加用户消息
        self._messages.append({"role": "user", "content": user_message})

        # 获取要发送给 LLM 的工具定义
        tool_defs = self._tools.get_definitions() or None

        final_content = ""
        for iteration in range(1, self._max_iterations + 1):
            response = self._chat_stream(tool_defs, on_content_delta)

            # 构建助手消息（可能包含 tool_calls）
            assistant_msg: dict[str, Any] = {"role": "assistant"}
            if response.content:
                assistant_msg["content"] = response.content
            if response.has_tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in response.tool_calls
                ]
            if not response.content and not response.has_tool_calls:
                assistant_msg["content"] = ""
            self._messages.append(assistant_msg)

            if not response.has_tool_calls:
                final_content = response.content or ""
                break

            # 执行工具并追加结果
            # （真实代码中的 agent.py 使用 asyncio.gather 并发执行）
            for tc in response.tool_calls:
                print(f"\n[calls {tc.name}({tc.arguments})]")
                result = asyncio.run(self._execute_tool(tc))
                self._messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })
        else:
            final_content = (
                "I have reached the maximum number of tool iterations. "
                "Please try simplifying your request."
            )

        return final_content

    async def _execute_tool(self, tc: ToolCallRequest) -> str:
        """执行单个工具调用。

        取自 ultrabot/agent.py 第 180-233 行。
        """
        tool = self._tools.get(tc.name)
        if tool is None:
            return f"Error: unknown tool '{tc.name}'"

        try:
            return await tool.execute(tc.arguments)
        except Exception as exc:
            return f"Error executing '{tc.name}': {type(exc).__name__}: {exc}"

    def _chat_stream(
        self,
        tools: list[dict] | None,
        on_content_delta: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        """调用 LLM 进行流式输出，从增量数据中组装工具调用。

        对应 ultrabot/providers/openai_compat.py
        第 109-200 行的流式输出逻辑。
        """
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": self._messages,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools

        stream = self._client.chat.completions.create(**kwargs)

        content_parts: list[str] = []
        tool_call_map: dict[int, dict[str, Any]] = {}

        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            # 内容 token
            if delta.content:
                content_parts.append(delta.content)
                if on_content_delta:
                    on_content_delta(delta.content)

            # 工具调用增量（以流式方式增量传输）
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_call_map:
                        tool_call_map[idx] = {
                            "id": tc_delta.id or "",
                            "name": "",
                            "arguments": "",
                        }
                    entry = tool_call_map[idx]
                    if tc_delta.id:
                        entry["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            entry["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            entry["arguments"] += tc_delta.function.arguments

        # 从累积的片段中组装完整的工具调用
        tool_calls = []
        for idx in sorted(tool_call_map):
            entry = tool_call_map[idx]
            try:
                args = json.loads(entry["arguments"]) if entry["arguments"] else {}
            except json.JSONDecodeError:
                args = {"_raw": entry["arguments"]}
            tool_calls.append(ToolCallRequest(
                id=entry["id"],
                name=entry["name"],
                arguments=args,
            ))

        return LLMResponse(
            content="".join(content_parts) or None,
            tool_calls=tool_calls,
        )

    def clear(self) -> None:
        """重置对话历史。"""
        self._messages = [{"role": "system", "content": self._system_prompt}]
