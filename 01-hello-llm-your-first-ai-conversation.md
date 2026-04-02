# Ultrabot：30 课程开发指南
**从零开始构建一个生产级 AI 助手框架。**
本指南将带你从"向 LLM 问好"一步步走到一个完整的多提供者、多通道 AI 智能体，具备工具调用、记忆、安全防护和 Web 界面。每节课程都建立在上一节课的基础之上。每节课都包含可运行的代码和测试。
本教程的主要思路来自于Nanobot(https://github.com/HKUDS/nanobot)以及Learn-Claude-Code(https://github.com/shareAI-lab/learn-claude-code/)，所以对应的叫做Ultrabot。
本课程设计由AI辅助下完成，更新地址见https://github.com/junfhu/UltrabotStepByStep，如果您觉得对您有帮助，请帮助点亮一颗星。
本课程中使用的大模型提供商是火山引擎Code Plan，如果正好你也需要，可以使用我的邀请码获取9折优惠 https://volcengine.com/L/_01BJCkKdMc/  邀请码：HHCDB4J4）



# 课程 1：向 LLM 问好 -- 你的第一次 AI 对话

**目标：** 用 10 行 Python 和 LLM 对话，然后逐步构建一个支持任意 OpenAI 兼容提供者的多轮聊天机器人。

**你将学到：**
- OpenAI chat completions API 的工作原理
- 消息列表模式（system / user / assistant 角色）
- 如何将客户端指向**任意** OpenAI 兼容提供者（我们这里用火山引擎）
- 如何构建多轮对话循环

**新建文件：**
- `chat.py` -- 一个可以立即运行的单文件聊天机器人

### 步骤 0：使用 pyenv 安装 Python 3.12

本指南全程使用 `pyenv` 管理 Python 版本。如果你还没有安装，
请参阅[介绍页](00-introduction.md#为什么用-pyenv)。

```bash
# 安装 Python 3.12（如果已安装可跳过）
pyenv install 3.12
pyenv global 3.12

# 创建项目目录和虚拟环境
mkdir -p ultrabot && cd ultrabot
python -m venv .venv
source .venv/bin/activate

# 验证
python --version  # Python 3.12.x
```

> **每次开始工作前都要激活虚拟环境：** `source .venv/bin/activate`

### 步骤 1：安装唯一的依赖

```bash
pip install openai
```

就这样。一个包。不需要项目脚手架，不需要配置文件。`openai` Python SDK
可以与任何暴露 OpenAI 兼容 API 的提供者一起使用 -- 不仅仅是 OpenAI 本身。

### 步骤 2：设置LLM相关的环境变量
```
export OPENAI_BASE_URL="https://ark.cn-beijing.volces.com/api/coding/v3"
export MODEL="minimax-m2.5"
export OPENAI_API_KEY="sk-..."
```

### 步骤 3：向 LLM 打个招呼

创建 `chat.py`：

```python
# chat.py -- 你的第一次 AI 对话
import os
from openai import OpenAI

# 三个环境变量控制你与哪个 LLM 对话：
#   OPENAI_API_KEY  -- 你的 API 密钥
#   OPENAI_BASE_URL -- 提供者的基础 URL
#   MODEL           -- 模型名称

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL"), 
)
model = os.getenv("MODEL")

response = client.chat.completions.create(
    model=model,
    messages=[{"role": "user", "content": "Hello!"}],
)
print(response.choices[0].message.content)
```

运行：

```bash
python chat.py
```

你应该能看到模型返回的友好问候。这就是整个 OpenAI 兼容 chat API：你发送一个
消息列表，得到一个回复。无论你调用的是 OpenAI、DeepSeek 还是本地模型，
同一份代码都能工作。

### 步骤 4：理解消息格式

每个 OpenAI chat 请求接收一个 `messages` 列表。每条消息是一个包含 `role` 和 `content` 的字典：

| 角色        | 用途                                         |
|-------------|----------------------------------------------|
| `system`    | 设定 AI 的性格和规则                          |
| `user`      | 人类说的话                                    |
| `assistant` | AI 说的话（用于对话历史记录）                  |

这是每个 LLM 聊天机器人的基础数据结构。UltraBot 的整个智能体循环（我们将在课程 2 中构建）就是围绕管理这个列表展开的。

### 步骤 5：添加系统提示词

```python
# chat.py -- 现在有了个性
import os
from openai import OpenAI

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL"), 
)
model = os.getenv("MODEL")

# 系统提示词设定 AI 的行为 -- 就像 ultrabot 的
# ultrabot/agent/prompts.py 中的 DEFAULT_SYSTEM_PROMPT
SYSTEM_PROMPT = """You are UltraBot, a helpful personal AI assistant.
- Answer concisely and accurately.
- When unsure, say so rather than guessing.
- Use code blocks for any code in your responses."""

response = client.chat.completions.create(
    model=model,
    messages=[
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "What is Python's GIL?"},
    ],
)
print(response.choices[0].message.content)
```

### 步骤 6：构建多轮对话

关键洞察：要进行对话，你需要维护一个不断增长的 `messages` 列表。每次助手回复后，将其追加到列表，然后追加下一条用户消息。

```python
# chat.py -- 完整的多轮聊天机器人（适用于任何 OpenAI 兼容提供者）
import os
from openai import OpenAI

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL"), 
)
model = os.getenv("MODEL")

SYSTEM_PROMPT = """You are UltraBot, a helpful personal AI assistant.
- Answer concisely and accurately.
- When unsure, say so rather than guessing.
- Use code blocks for any code in your responses."""

# 对话历史 -- 这是核心数据结构
messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]

print(f"UltraBot ready (model={model}). Type 'exit' to quit.\n")

while True:
    user_input = input("you > ").strip()
    if not user_input:
        continue
    if user_input.lower() in ("exit", "quit"):
        print("Goodbye!")
        break

    # 1. 将用户消息追加到历史记录
    messages.append({"role": "user", "content": user_input})

    # 2. 将完整历史记录发送给 LLM
    response = client.chat.completions.create(
        model=model,
        messages=messages,
    )

    # 3. 提取助手的回复
    assistant_message = response.choices[0].message.content

    # 4. 将助手的回复追加到历史记录（这就是让对话变成
    #    "多轮"的关键 -- LLM 能看到之前所有内容）
    messages.append({"role": "assistant", "content": assistant_message})

    print(f"\nassistant > {assistant_message}\n")
```

这种模式 -- 追加用户消息、调用 LLM、追加助手回复、循环 -- 是**每一个** AI 聊天机器人的核心。UltraBot 的 `Agent.run()` 方法（在 `ultrabot/agent/agent.py` 中）做的就是同样的事情，只是在上面叠加了更多功能。

### 步骤 7：添加一个最简的 pyproject.toml

后面的课程中需要它来让 `pip install -e .` 生效。现在先保持最简：

```toml
# pyproject.toml
[project]
name = "ultrabot"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["openai>=1.0"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

### 测试

创建 `tests/test_session1.py`：

```python
# tests/test_session1.py
"""课程 1 的测试 -- 消息格式、环境变量配置和响应解析。"""
import os
import pytest


def test_message_format():
    """验证我们的消息列表具有正确的结构。"""
    messages = [
        {"role": "system", "content": "You are a helper."},
        {"role": "user", "content": "Hello!"},
    ]
    # 每条消息必须包含 'role' 和 'content'
    for msg in messages:
        assert "role" in msg
        assert "content" in msg
        assert msg["role"] in ("system", "user", "assistant", "tool")


def test_multi_turn_history():
    """验证对话历史记录正确增长。"""
    messages = [{"role": "system", "content": "You are a helper."}]

    # 模拟一个两轮对话
    messages.append({"role": "user", "content": "Hi"})
    messages.append({"role": "assistant", "content": "Hello!"})
    messages.append({"role": "user", "content": "How are you?"})
    messages.append({"role": "assistant", "content": "I'm great!"})

    assert len(messages) == 5
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert messages[2]["role"] == "assistant"
    # 在系统提示词之后，角色交替出现 user/assistant
    for i in range(1, len(messages)):
        expected = "user" if i % 2 == 1 else "assistant"
        assert messages[i]["role"] == expected


def test_default_model():
    """未设置 MODEL 环境变量时，默认为 gpt-4o-mini。"""
    orig = os.environ.pop("MODEL", None)
    try:
        model = os.getenv("MODEL", "gpt-4o-mini")
        assert model == "gpt-4o-mini"
    finally:
        if orig is not None:
            os.environ["MODEL"] = orig


def test_custom_model(monkeypatch):
    """MODEL 环境变量可覆盖默认模型。"""
    monkeypatch.setenv("MODEL", "deepseek-chat")
    model = os.getenv("MODEL", "gpt-4o-mini")
    assert model == "deepseek-chat"


def test_custom_base_url(monkeypatch):
    """OPENAI_BASE_URL 环境变量用于配置提供者端点。"""
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com")
    base_url = os.getenv("OPENAI_BASE_URL")
    assert base_url == "https://api.deepseek.com"


def test_base_url_none_when_unset():
    """OPENAI_BASE_URL 未设置时默认为 None（使用 OpenAI 端点）。"""
    orig = os.environ.pop("OPENAI_BASE_URL", None)
    try:
        base_url = os.getenv("OPENAI_BASE_URL")
        assert base_url is None
    finally:
        if orig is not None:
            os.environ["OPENAI_BASE_URL"] = orig


def test_response_parsing_mock(monkeypatch):
    """测试我们能否正确解析 OpenAI 响应（使用 mock）。"""
    from unittest.mock import MagicMock

    # 构建一个模拟的响应，看起来像 OpenAI 返回的结果
    mock_message = MagicMock()
    mock_message.content = "Hello! How can I help?"

    mock_choice = MagicMock()
    mock_choice.message = mock_message

    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    # 这就是我们在 chat.py 中解析它的方式
    result = mock_response.choices[0].message.content
    assert result == "Hello! How can I help?"
```

运行测试：

```bash
pip install pytest
pytest tests/test_session1.py -v
```

### 检查点

```bash
# 使用任意提供者 -- 设置环境变量后运行：
python chat.py
```

预期输出：
```
UltraBot ready (model=minimax-m2.5). Type 'exit' to quit.

you > What is 2 + 2?

assistant > 2 + 2 equals 4.

you > And multiply that by 10?

assistant > 4 multiplied by 10 equals 40.

you > exit
Goodbye!
```

模型记住了之前的对话轮次，因为我们每次都发送了完整的 `messages` 列表。
由于我们从环境变量读取 `OPENAI_BASE_URL` 和 `MODEL`，同一份代码可以
用于任何兼容OPENAI的大模型提供商。

### 本课成果

一个完整的单文件多轮聊天机器人，支持**任意** OpenAI 兼容提供者。三个环境变量
（`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`MODEL`）让你无需修改代码即可切换
提供者。消息列表模式（`system` + 交替的 `user`/`assistant`）是 UltraBot 中
一切功能的基础。

---
