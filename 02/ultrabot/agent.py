# ultrabot/agent.py
"""核心智能体循环 -- 编排 LLM 调用和对话状态。

为教学目的简化自 ultrabot/agent/agent.py。
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from typing import Any, Callable

from openai import OpenAI


# -- 数据类（与 ultrabot/providers/base.py 相同的模式）--

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
"""


class Agent:
    """管理对话状态并驱动 LLM 调用循环的高层智能体。

    这是 ultrabot.agent.agent.Agent 的简化版本。
    真实版本还包含工具执行、安全守卫和会话持久化
    -- 我们将在后面的课程中添加这些。
    """

    def __init__(
        self,
        client: OpenAI,
        model: str,
        system_prompt: str = SYSTEM_PROMPT,
        max_iterations: int = 10,
    ) -> None:
        self._client = client
        self._model = model
        self._system_prompt = system_prompt
        self._max_iterations = max_iterations

        # 对话历史（对应真实代码中的 session.get_messages()）
        self._messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt}
        ]

    def run(
        self,
        user_message: str,
        on_content_delta: Callable[[str], None] | None = None,
    ) -> str:
        """处理用户消息并返回助手的回复。

        这是 ultrabot/agent/agent.py 第 65-174 行的核心智能体循环。
        真实版本是异步的并支持工具调用 -- 我们后面会实现。

        参数
        ----------
        user_message:
            用户说了什么。
        on_content_delta:
            可选的回调函数，每个流式文本片段到达时调用。
            CLI 就是通过这个来实时显示 token 的。
        """
        # 1. 追加用户消息
        self._messages.append({"role": "user", "content": user_message})

        # 2. 进入智能体循环
        #    在课程 3 中我们会在这里添加工具调用。目前循环
        #    总是在第一次迭代时退出（没有工具 = 最终答案）。
        final_content = ""
        for iteration in range(1, self._max_iterations + 1):
            # 调用 LLM 进行流式输出
            response = self._chat_stream(on_content_delta)

            # 将助手消息追加到历史记录
            self._messages.append({
                "role": "assistant",
                "content": response.content or "",
            })

            if not response.has_tool_calls:
                # 没有工具调用 -- 这就是最终答案
                final_content = response.content or ""
                break

            # （工具执行将在课程 3 中添加到这里）
        else:
            # 安全阀：耗尽了所有迭代次数
            final_content = (
                "I have reached the maximum number of iterations. "
                "Please try simplifying your request."
            )

        return final_content

    def _chat_stream(
        self,
        on_content_delta: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        """向 LLM 发送消息并启用流式输出。

        对应 ultrabot/providers/openai_compat.py
        第 109-200 行的流式输出逻辑（chat_stream 方法）。
        """
        stream = self._client.chat.completions.create(
            model=self._model,
            messages=self._messages,
            stream=True,
        )

        content_parts: list[str] = []
        tool_calls: list[dict] = []

        for chunk in stream:
            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta

            # -- 内容增量 --
            if delta.content:
                content_parts.append(delta.content)
                if on_content_delta:
                    on_content_delta(delta.content)

            # -- 工具调用增量（我们将在课程 3 中使用）--
            # 目前 tool_calls 保持为空。

        return LLMResponse(
            content="".join(content_parts) or None,
            tool_calls=tool_calls,
        )

    def clear(self) -> None:
        """重置对话历史。"""
        self._messages = [{"role": "system", "content": self._system_prompt}]
