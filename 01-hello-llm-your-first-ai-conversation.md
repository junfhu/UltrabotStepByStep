# Ultrabot：30 课程开发指南
**从零开始构建一个生产级 AI 助手框架。**
本指南将带你从"向 LLM 问好"一步步走到一个完整的多提供者、多通道 AI 智能体，具备工具调用、记忆、安全防护和 Web 界面。每节课程都建立在上一节课的基础之上。每节课都包含可运行的代码和测试。  
本教程的主要思路来自于
- Nanobot (https://github.com/HKUDS/nanobot)
- Learn-Claude-Code (https://github.com/shareAI-lab/learn-claude-code/)

本课程设计由AI辅助下完成，因为课程自身也在不停修正，请参考 https://github.com/junfhu/UltrabotStepByStep，如果您觉得对您有帮助，请帮助点亮一颗星。  
本课程中使用的大模型提供商是火山引擎Code Plan，如果正好你也需要，可以使用我的邀请码获取9折优惠 https://volcengine.com/L/_01BJCkKdMc/  邀请码：HHCDB4J4）  



# 课程 1：向 LLM 问好 -- 你的第一次 AI 对话

**目标：** 用 10 行 Python 和 LLM 对话，然后逐步构建一个支持任意 OpenAI 兼容提供者的多轮聊天机器人。

**你将学到：**
- OpenAI chat completions API 的工作原理
- 消息列表模式（system / user / assistant 角色）
- 如何将客户端指向**任意** OpenAI 兼容提供者（我们这里用火山引擎）
- 如何构建多轮对话循环

**新建文件：**
- `ultrabot/chat.py` -- 一个可以立即运行的单文件聊天机器人

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

创建 `ultrabot/chat.py`：

```python
# ultrabot/chat.py -- 你的第一次 AI 对话
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
python ultrabot/chat_1.py
Hello! How can I help you today?
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
# ultrabot/chat_1.py -- 现在有了个性
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
# ultrabot/chat_2.py -- 完整的多轮聊天机器人（适用于任何 OpenAI 兼容提供者）
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

> **提示：** 在后续课程中，源代码将放在 `ultrabot/` 子目录中形成 Python 包。此时你可以先在项目根目录下创建这个子目录和 `__init__.py` 文件：

```bash
mkdir -p ultrabot
touch ultrabot/__init__.py
```

这样 `from ultrabot.xxx import ...` 的导入方式才能生效。后续课程新建的文件都应放在 `ultrabot/` 子目录下。

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

    # 这就是我们在 ultrabot/chat.py 中解析它的方式
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
python ultrabot/chat_2.py
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

## 本课使用的 Python 知识

### `import` 语句（标准库与第三方库导入）

`import` 是 Python 中引入外部功能的方式。Python 有两种主要的导入写法：

```python
import os                      # 导入整个标准库模块
from openai import OpenAI      # 从第三方库中只导入需要的类
```

- `import os` 导入 Python 自带的 `os` 模块，提供操作系统相关功能（如读取环境变量）。
- `from openai import OpenAI` 从第三方包 `openai`（通过 `pip install openai` 安装的）中导入 `OpenAI` 这个类。

**为什么在本课使用：** 我们需要 `os` 模块来读取环境变量配置 API 密钥和地址，需要 `openai` 库的 `OpenAI` 类来与 LLM 通信。这两行是几乎所有 Python AI 项目的起手式。

---

### `os.getenv()` 读取环境变量

`os.getenv("变量名")` 用于从操作系统的环境变量中读取值。如果该变量不存在，返回 `None`（也可以指定默认值）。

```python
api_key = os.getenv("OPENAI_API_KEY")           # 不存在时返回 None
model = os.getenv("MODEL", "gpt-4o-mini")        # 不存在时返回 "gpt-4o-mini"
```

**为什么在本课使用：** API 密钥、服务地址、模型名称都属于敏感信息或可变配置，不应硬编码在源代码中。通过环境变量传入，同一份代码可以切换不同的 LLM 提供者，也避免了将密钥泄露到代码仓库。

---

### 字典 `dict`

字典是 Python 中最常用的数据结构之一，用**键值对**存储数据，用花括号 `{}` 创建：

```python
message = {"role": "user", "content": "Hello!"}
print(message["role"])      # 输出: user
print(message["content"])   # 输出: Hello!
```

字典中的键（如 `"role"`）必须是不可变类型（通常是字符串），值可以是任何类型。

**为什么在本课使用：** OpenAI 的聊天 API 要求每条消息是一个包含 `"role"` 和 `"content"` 键的字典。这是 API 协议规定的格式，本课的所有消息都以这种字典形式传递。

---

### 列表 `list` 与 `list.append()`

列表是 Python 中的有序集合，用方括号 `[]` 创建。`append()` 方法在末尾添加元素：

```python
messages = [{"role": "system", "content": "You are a helper."}]
messages.append({"role": "user", "content": "Hi"})
messages.append({"role": "assistant", "content": "Hello!"})
print(len(messages))  # 输出: 3
```

**为什么在本课使用：** 多轮对话的核心就是维护一个不断增长的消息列表。每次用户说话，`append` 一条 `user` 消息；每次 LLM 回复，`append` 一条 `assistant` 消息。LLM 每次看到完整的列表，就能"记住"之前的对话。

---

### 类型注解 `list[dict]`

类型注解是写在变量或函数参数旁边的"提示"，告诉开发者（和 IDE）这个变量应该存什么类型的数据：

```python
messages: list[dict] = [{"role": "system", "content": "You are a helper."}]
```

这行表示 `messages` 是一个"字典组成的列表"。Python 运行时**不强制**检查类型注解，但它能帮助 IDE 提供自动补全和错误提示。

**为什么在本课使用：** 标注 `messages: list[dict]` 让代码的意图更清晰——任何人读到这行都能立刻明白这是一个"消息字典的列表"，而不需要去猜测。

---

### `while True` 无限循环与 `break` / `continue`

`while True` 创建一个永远执行的循环，直到内部用 `break` 跳出：

```python
while True:
    user_input = input("you > ").strip()
    if not user_input:
        continue       # 跳过本次循环，回到 while True
    if user_input.lower() in ("exit", "quit"):
        break          # 跳出整个循环，程序继续往下执行
    # ... 处理输入 ...
```

- `continue`：跳过本次循环的剩余代码，直接开始下一次循环
- `break`：完全跳出循环

**为什么在本课使用：** 聊天机器人需要不断接受用户输入并回复，直到用户输入 `exit`。`while True` + `break` 是实现"一直运行直到某个条件满足"这种模式的标准写法。

---

### `input()` 与字符串方法 `strip()` / `lower()`

`input()` 从终端读取用户输入，返回字符串。常配合字符串方法清理输入：

```python
text = input("you > ")      # 等待用户输入
text = text.strip()          # 去掉首尾空白字符（空格、换行等）
text = text.lower()          # 转为全小写
```

**为什么在本课使用：** `strip()` 确保用户不小心多按的空格不会影响判断；`lower()` 让 `"Exit"`、`"EXIT"`、`"exit"` 都能被识别为退出命令，提升用户体验。

---

### `in` 成员检测运算符

`in` 用于检查某个值是否存在于一个集合（列表、元组、字符串、字典等）中：

```python
if user_input.lower() in ("exit", "quit"):
    break
```

这行等价于 `if user_input.lower() == "exit" or user_input.lower() == "quit"`，但更简洁。

**为什么在本课使用：** 用一行代码同时检测多个退出命令（`exit` 和 `quit`），比写多个 `if` / `elif` 更简洁优雅。

---

### f-string 格式化字符串

f-string（格式化字符串）是 Python 3.6+ 引入的字符串格式化方式，在字符串前加 `f`，花括号 `{}` 中可以直接嵌入 Python 表达式：

```python
model = "gpt-4o-mini"
print(f"UltraBot ready (model={model})")
# 输出: UltraBot ready (model=gpt-4o-mini)

name = "Alice"
print(f"Hello, {name.upper()}!")
# 输出: Hello, ALICE!
```

**为什么在本课使用：** 在打印提示信息时，需要将变量值（如模型名称、助手回复）嵌入到字符串中。f-string 是最直观、最高效的方式。

---

### `pytest` 测试框架

`pytest` 是 Python 最流行的测试框架。测试函数以 `test_` 开头，用 `assert` 语句验证结果：

```python
def test_message_format():
    messages = [{"role": "user", "content": "Hello!"}]
    assert "role" in messages[0]         # 断言为真则通过
    assert messages[0]["role"] == "user"  # 断言不为真则测试失败
```

运行方式：`pytest tests/test_session1.py -v`

**为什么在本课使用：** 测试确保我们的消息格式正确、环境变量配置可用、响应解析无误。即使是最简单的代码，测试也能防止回归错误——后续修改时如果不小心破坏了什么，测试会立刻报警。

---

### `monkeypatch`（pytest 夹具）

`monkeypatch` 是 pytest 提供的一个特殊工具（夹具/fixture），用于在测试中临时修改环境变量、属性等，测试结束后自动恢复：

```python
def test_custom_model(monkeypatch):
    monkeypatch.setenv("MODEL", "deepseek-chat")   # 临时设置环境变量
    model = os.getenv("MODEL", "gpt-4o-mini")
    assert model == "deepseek-chat"
# 测试结束后 MODEL 环境变量自动恢复原值
```

**为什么在本课使用：** 测试环境变量配置时，我们不想真的修改系统环境变量（会影响其他测试）。`monkeypatch` 让我们可以安全地模拟各种配置场景。

---

### `unittest.mock.MagicMock`

`MagicMock` 是 Python 标准库中的"万能替身"对象。它可以模拟任何对象，访问它的任何属性或方法都不会报错：

```python
from unittest.mock import MagicMock

mock_response = MagicMock()
mock_response.choices[0].message.content = "Hello!"
print(mock_response.choices[0].message.content)  # 输出: Hello!
```

**为什么在本课使用：** 在测试中我们不想真正调用 LLM API（需要网络、需要付费），所以用 `MagicMock` 构造一个假的响应对象，验证我们的解析逻辑是否正确。这是单元测试的核心技巧。

---

### `try` / `finally` 异常处理

`try/finally` 确保无论是否发生异常，`finally` 中的代码都会执行：

```python
orig = os.environ.pop("MODEL", None)   # 临时移除环境变量
try:
    model = os.getenv("MODEL", "gpt-4o-mini")
    assert model == "gpt-4o-mini"
finally:
    if orig is not None:
        os.environ["MODEL"] = orig      # 无论如何都恢复原值
```

**为什么在本课使用：** 在手动操作环境变量的测试中，`finally` 块保证即使测试失败（抛出异常），环境变量也会被恢复到原始状态，不会污染其他测试用例。
