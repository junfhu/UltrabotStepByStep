# Ultrabot：30 课程开发指南
**从零开始构建一个生产级 AI 助手框架。**
本指南将带你从"向 LLM 问好"一步步走到一个完整的多提供者、多通道 AI 智能体，具备工具调用、记忆、安全防护和 Web 界面。每节课程都建立在上一节课的基础之上。每节课都包含可运行的代码和测试。  
本教程的主要思路来自于
- Nanobot (https://github.com/HKUDS/nanobot)
- Learn-Claude-Code (https://github.com/shareAI-lab/learn-claude-code/)

本课程设计由AI辅助下完成，因为课程自身也在不停修正，请参考 https://github.com/junfhu/UltrabotStepByStep，如果您觉得对您有帮助，请帮助点亮一颗星。  
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

## 本课使用的 Python 知识

### `from __future__ import annotations`（延迟注解求值）

这行写在文件最顶部，让 Python 不会在定义时立即解析类型注解，而是将它们当作字符串存储：

```python
from __future__ import annotations

class Agent:
    def run(self) -> str:   # "str" 不会在定义时被求值
        ...
```

**为什么在本课使用：** 它允许我们在类型注解中使用尚未定义的类名（前向引用），也让 `str | None` 这种新语法在 Python 3.9 等较老版本中也能工作。这是现代 Python 项目的常见做法。

---

### `@dataclass` 数据类

`dataclass` 是 Python 3.7+ 引入的装饰器，自动为类生成 `__init__`、`__repr__` 等方法，让你用最少的代码定义数据容器：

```python
from dataclasses import dataclass, field

@dataclass
class LLMResponse:
    content: str | None = None
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
```

等价于手写一个包含 `__init__`、`__repr__` 等方法的类，但代码量少了一半以上。

**为什么在本课使用：** `LLMResponse` 只需要存储几个字段，没有复杂逻辑。用 `@dataclass` 可以用三行声明代替十几行模板代码，让代码更清晰。

---

### `field(default_factory=...)` 数据类默认工厂

在 `@dataclass` 中，如果默认值是可变对象（如列表、字典），必须使用 `field(default_factory=...)` 而不是直接赋值：

```python
# 错误！所有实例会共享同一个列表
# tool_calls: list[dict] = []

# 正确！每个实例创建独立的新列表
tool_calls: list[dict] = field(default_factory=list)
```

**为什么在本课使用：** `LLMResponse` 的 `tool_calls` 和 `usage` 字段默认为空列表和空字典。如果不用 `default_factory`，所有 `LLMResponse` 实例会共享同一个列表/字典，修改一个会影响所有——这是 Python 中非常常见的陷阱。

---

### `typing` 模块：`Any` 和 `Callable`

`typing` 模块提供高级类型注解工具：

```python
from typing import Any, Callable

# Any 表示"任意类型"
usage: dict[str, Any] = {}    # 值可以是字符串、数字、列表……任何东西

# Callable[[参数类型], 返回类型] 表示一个可调用对象（函数）
on_content_delta: Callable[[str], None]   # 接收一个字符串，返回 None 的函数
```

**为什么在本课使用：** `Any` 用于 `usage` 字典（其值类型不固定）；`Callable` 用于流式输出回调函数参数——告诉读者"这个参数需要传入一个接收字符串的函数"。

---

### `str | None` 联合类型（PEP 604）

`str | None` 表示一个值可以是字符串，也可以是 `None`：

```python
content: str | None = None     # content 可能是字符串，也可能是 None
```

这是 Python 3.10+ 引入的语法，等价于旧写法 `Optional[str]` 或 `Union[str, None]`。

**为什么在本课使用：** LLM 的响应内容可能为空（比如只返回工具调用没有文本），所以 `content` 需要允许 `None` 值。`str | None` 明确表达了这种可能性。

---

### `@property` 属性装饰器

`@property` 把一个方法变成"看起来像属性"的东西——访问时不需要加括号：

```python
@dataclass
class LLMResponse:
    tool_calls: list[dict] = field(default_factory=list)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)

response = LLMResponse(tool_calls=[{"id": "1"}])
print(response.has_tool_calls)  # True（注意没有括号）
```

**为什么在本课使用：** `has_tool_calls` 本质是一个计算属性（根据 `tool_calls` 是否为空推导出来的），用 `@property` 让调用代码写成 `response.has_tool_calls` 而不是 `response.has_tool_calls()`，更自然、更像访问一个状态。

---

### 类（`class`）与面向对象编程

类是 Python 面向对象编程的基础，将数据（属性）和操作数据的方法（函数）封装在一起：

```python
class Agent:
    def __init__(self, client, model):
        self._client = client     # 实例属性
        self._model = model

    def run(self, user_message):  # 实例方法
        ...

    def clear(self):
        ...
```

**为什么在本课使用：** 智能体需要维护对话状态（`_messages` 列表）、持有 LLM 客户端引用、提供多个操作方法。将这些封装进 `Agent` 类中，比在全局变量中管理状态更清晰、更安全、更易复用。

---

### `__init__` 构造函数与 `self`

`__init__` 是创建类实例时自动调用的特殊方法。`self` 是实例自身的引用：

```python
class Agent:
    def __init__(self, client: OpenAI, model: str) -> None:
        self._client = client     # 将参数保存为实例属性
        self._model = model
        self._messages = []       # 初始化内部状态

agent = Agent(client=my_client, model="gpt-4o")  # 自动调用 __init__
```

**为什么在本课使用：** Agent 的 `__init__` 接收并保存客户端、模型名、系统提示词等配置，并初始化消息列表。这些都是实例级别的状态，每个 Agent 对象独立维护。

---

### `_` 前缀命名惯例（私有属性）

Python 中以单下划线 `_` 开头的属性或方法表示"仅供内部使用"：

```python
class Agent:
    def __init__(self):
        self._messages = []      # 私有属性，外部不应直接访问
        self._client = ...

    def _chat_stream(self):      # 私有方法，外部不应直接调用
        ...

    def run(self):               # 公共方法，供外部调用
        ...
```

这只是命名惯例，Python 不会真的阻止外部访问，但它传达了"这是内部实现细节"的意图。

**为什么在本课使用：** `_messages`、`_client`、`_chat_stream` 等是 Agent 的内部实现细节，外部代码应该通过 `run()` 和 `clear()` 等公共方法来交互。下划线前缀让代码的公共接口和内部实现一目了然。

---

### `for ... else` 循环

Python 独有的语法：`for` 循环正常结束（没被 `break` 打断）时，执行 `else` 块：

```python
for iteration in range(1, max_iterations + 1):
    response = self._chat_stream(on_content_delta)
    if not response.has_tool_calls:
        break                    # 有最终答案，跳出循环
else:
    # 只有循环跑完所有迭代都没 break 时才执行这里
    final_content = "达到最大迭代次数"
```

**为什么在本课使用：** 智能体循环需要一个"安全阀"——如果 LLM 一直请求工具调用，循环耗尽所有迭代后，`else` 块返回一个安全提示，避免无限循环。

---

### `range()` 范围函数

`range()` 生成一个整数序列，常用于 `for` 循环：

```python
for i in range(1, 11):    # 生成 1, 2, 3, ..., 10
    print(i)

for i in range(5):         # 生成 0, 1, 2, 3, 4
    print(i)
```

**为什么在本课使用：** `range(1, self._max_iterations + 1)` 控制智能体循环最多执行指定次数，防止在工具调用出问题时无限循环。

---

### `"".join()` 字符串拼接

`"".join(列表)` 将字符串列表高效地拼接成一个字符串：

```python
parts = ["Hello", " ", "world", "!"]
result = "".join(parts)     # "Hello world!"
```

**为什么在本课使用：** 流式输出时，我们把每个小片段（token）收集到 `content_parts` 列表中，最后用 `"".join(content_parts)` 一次性拼接成完整响应。这比用 `+=` 反复拼接字符串效率高得多。

---

### `lambda` 匿名函数

`lambda` 创建一个简短的、没有名字的函数，适合只使用一次的简单逻辑：

```python
# 完整写法
def print_chunk(chunk):
    print(chunk, end="", flush=True)

# lambda 等价写法
print_chunk = lambda chunk: print(chunk, end="", flush=True)

# 常见用法：直接作为参数传递
agent.run("Hi", on_content_delta=lambda chunk: print(chunk, end="", flush=True))
```

**为什么在本课使用：** 流式输出回调只需要一行代码（打印 token），为它单独定义一个函数太冗余。`lambda` 让我们在调用处直接定义这个简单逻辑。

---

### `print()` 的 `end` 和 `flush` 参数

`print()` 默认在末尾加换行符。通过 `end=""` 可以取消换行，`flush=True` 强制立即输出：

```python
print("Hello ", end="")       # 不换行
print("world!", end="")      # 不换行
print()                        # 输出: Hello world!（最后换行）

# flush=True 强制立即写入终端（不等缓冲区满）
print("loading...", end="", flush=True)
```

**为什么在本课使用：** 流式输出需要每个 token 到达时立刻显示在同一行上，模拟"打字效果"。`end=""` 避免每个 token 换行，`flush=True` 确保 token 立即可见而不是积攒在缓冲区中。

---

### 回调函数模式（`on_content_delta`）

回调函数是一种设计模式：你把一个函数作为参数传给另一个函数，后者在特定事件发生时"回调"它：

```python
def run(self, user_message, on_content_delta=None):
    ...
    if on_content_delta:
        on_content_delta(delta.content)   # 每收到一个 token 就调用回调

# 使用时传入你想执行的逻辑
agent.run("Hi", on_content_delta=lambda chunk: print(chunk, end=""))
```

**为什么在本课使用：** Agent 类不应该直接 `print`——它可能在 CLI、Web、测试等不同环境中使用。回调模式让调用者决定如何处理流式数据：CLI 可以打印到终端，Web 可以推送到浏览器，测试可以收集到列表中。这是**关注点分离**的体现。

---

### 流式迭代器（`stream=True`）

OpenAI SDK 的 `stream=True` 参数让 API 返回一个迭代器，你可以用 `for` 循环逐个处理数据块（chunk），而不是等待完整响应：

```python
stream = client.chat.completions.create(
    model=model,
    messages=messages,
    stream=True,          # 关键参数
)

for chunk in stream:      # chunk 逐个到达
    delta = chunk.choices[0].delta
    if delta.content:
        print(delta.content, end="")
```

**为什么在本课使用：** LLM 生成长文本可能需要几秒钟。流式输出让用户在 LLM 还在生成时就能看到前面的内容，大幅提升交互体验——这就是 ChatGPT 逐字显示的原理。
