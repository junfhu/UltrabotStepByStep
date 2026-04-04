# Ultrabot：30 课程开发指南
**从零开始构建一个生产级 AI 助手框架。**
本指南将带你从"向 LLM 问好"一步步走到一个完整的多提供者、多通道 AI 智能体，具备工具调用、记忆、安全防护和 Web 界面。每节课程都建立在上一节课的基础之上。每节课都包含可运行的代码和测试。  
本教程的主要思路来自于Nanobot(https://github.com/HKUDS/nanobot)以及Learn-Claude-Code(https://github.com/shareAI-lab/learn-claude-code/)，所以对应的叫做Ultrabot。  
本课程设计由AI辅助下完成，因为课程自身也在不停修正，请参考https://github.com/junfhu/UltrabotStepByStep的最新版本，如果您觉得对您有帮助，请帮助点亮一颗星。  
本课程中使用的大模型提供商是火山引擎Code Plan，如果正好你也需要，可以使用我的邀请码获取9折优惠 https://volcengine.com/L/_01BJCkKdMc/  邀请码：HHCDB4J4）  



# 课程 2：流式输出 + 智能体循环

**目标：** 实时流式输出 token，并将聊天机器人重构为一个带有运行循环的 Agent 类。

**你将学到：**
- LLM 流式输出的工作原理（token 逐个到达）
- 智能体循环模式：系统提示词 -> 用户 -> LLM ->（工具？）-> 响应
- 最大迭代次数保护，防止无限循环
- 将关注点分离到 `Agent` 类中

**新建文件：**
- `ultrabot/agent.py` -- 带有 `run()` 方法的 Agent 类

### 步骤 1：为聊天机器人添加流式输出

与其等待完整的响应，我们可以在 token 到达时实时流式输出。这就是 ChatGPT 逐字显示文本的方式：

```python
# chat_stream.py -- 流式输出版本
import os
from openai import OpenAI

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL"),
)
model = os.getenv("MODEL")

SYSTEM_PROMPT = """You are UltraBot, a helpful personal AI assistant."""

messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]

print("UltraBot (streaming). Type 'exit' to quit.\n")

while True:
    user_input = input("you > ").strip()
    if not user_input:
        continue
    if user_input.lower() in ("exit", "quit"):
        break

    messages.append({"role": "user", "content": user_input})

    # stream=True 返回一个 chunk 迭代器，而不是一个完整的响应
    print("assistant > ", end="", flush=True)
    stream = client.chat.completions.create(
        model=model,
        messages=messages,
        stream=True,  # <-- 关键参数
    )

    # 在流式输出的同时收集完整响应
    full_response = ""
    for chunk in stream:
        # 每个 chunk 有一个 delta，包含一小段内容
        delta = chunk.choices[0].delta
        if delta.content:
            print(delta.content, end="", flush=True)
            full_response += delta.content

    print("\n")  # 流式输出完成后换行

    messages.append({"role": "assistant", "content": full_response})
```

关键区别：使用 `stream=True` 后，你会得到一个 `chunk` 对象的生成器。每个 chunk 的 `delta.content` 是一小段文本（通常是一个单词或一个 token）。立即打印它们，用户就能看到响应实时构建出来。

### 步骤 2：构建 Agent 类

现在让我们将循环逻辑提取到一个正式的类中。这对应了真实代码库中 `ultrabot/agent/agent.py`：

```python
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
```

### 步骤 3：使用 Agent

```python
# ultrabot/main.py -- 使用 Agent 类
import os
from openai import OpenAI
from ultrabot.agent import Agent

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL"),
)
model = os.getenv("MODEL", "gpt-4o-mini")

agent = Agent(
    client=client,
    model=model,
)

print("UltraBot (Agent class). Type 'exit' to quit.\n")

while True:
    user_input = input("you > ").strip()
    if not user_input:
        continue
    if user_input.lower() in ("exit", "quit"):
        print("Goodbye!")
        break

    # 流式输出回调在 token 到达时打印它们
    print("assistant > ", end="", flush=True)
    response = agent.run(
        user_input,
        on_content_delta=lambda chunk: print(chunk, end="", flush=True),
    )
    print("\n")

```

### 测试

```python
# tests/test_session2.py
"""课程 2 的测试 -- Agent 类和流式输出。"""
from unittest.mock import MagicMock

from ultrabot.agent import Agent, LLMResponse


def make_agent(model: str = "gpt-4o-mini", max_iterations: int = 10) -> Agent:
    """创建一个带 mock client 的 Agent，便于测试。"""
    client = MagicMock()
    return Agent(client=client, model=model, max_iterations=max_iterations)


def test_agent_init():
    """Agent 初始化时消息列表中包含系统提示词。"""
    agent = make_agent()

    assert len(agent._messages) == 1
    assert agent._messages[0]["role"] == "system"


def test_agent_appends_user_message():
    """Agent.run() 将用户消息和助手回复追加到历史记录。"""
    agent = make_agent()

    mock_response = LLMResponse(content="Hello!", tool_calls=[])
    agent._chat_stream = MagicMock(return_value=mock_response)

    result = agent.run("Hi there")

    assert result == "Hello!"
    assert len(agent._messages) == 3
    assert agent._messages[1] == {"role": "user", "content": "Hi there"}
    assert agent._messages[2] == {"role": "assistant", "content": "Hello!"}


def test_agent_max_iterations():
    """即使一直出现工具调用，Agent 也会在 max_iterations 后停止。"""
    agent = make_agent(max_iterations=2)

    response_with_tools = LLMResponse(
        content="",
        tool_calls=[{"id": "1", "function": {"name": "test", "arguments": "{}"}}],
    )
    agent._chat_stream = MagicMock(return_value=response_with_tools)

    result = agent.run("Do something")

    assert "maximum number of iterations" in result


def test_streaming_callback_is_forwarded():
    """run() 会把 on_content_delta 回调传给 _chat_stream。"""
    agent = make_agent()
    callback = MagicMock()

    mock_response = LLMResponse(content="Hello world", tool_calls=[])
    agent._chat_stream = MagicMock(return_value=mock_response)

    agent.run("Hi", on_content_delta=callback)

    agent._chat_stream.assert_called_once_with(callback)


def test_agent_clear():
    """Agent.clear() 重置为只包含系统提示词。"""
    agent = make_agent()
    mock_response = LLMResponse(content="Hi!", tool_calls=[])
    agent._chat_stream = MagicMock(return_value=mock_response)

    agent.run("Hello")
    assert len(agent._messages) == 3

    agent.clear()

    assert len(agent._messages) == 1
    assert agent._messages[0]["role"] == "system"

```

### 检查点
```bash
pip install -e .
python ultrabot/main.py
```

预期输出 -- token 实时流式输出：
```
UltraBot (Agent class). Type 'exit' to quit.

you > Write a haiku about Python

assistant > Indented with care,
Snakes of logic twist and turn,
Code blooms line by line.

you > exit
Goodbye!
```

你应该看到每个词逐个出现，而不是一次性全部显示。

### 本课成果

一个带有 `run()` 方法的 `Agent` 类，实现了核心智能体循环：追加用户消息 -> 流式调用 LLM -> 追加助手回复 -> 循环。最大迭代次数保护防止了无限循环。这是 `ultrabot/agent/agent.py` 的骨架 -- 下一节我们将添加工具调用。

---
