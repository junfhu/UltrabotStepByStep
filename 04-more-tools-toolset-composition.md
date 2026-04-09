# Agent: 30课程开发指南
**从零开始构建一个生产级 AI 助手框架。**
本指南将带你从"向 LLM 问好"一步步走到一个完整的多提供者、多通道 AI 智能体，具备工具调用、记忆、安全防护和 Web 界面。每节课程都建立在上一节课的基础之上。每节课都包含可运行的代码和测试。  
本教程的主要思路来自于
- Nanobot (https://github.com/HKUDS/nanobot)
- Learn-Claude-Code (https://github.com/shareAI-lab/learn-claude-code/)

本课程设计由AI辅助下完成，因为课程自身也在不停修正，请参考 https://github.com/junfhu/UltrabotStepByStep，如果您觉得对您有帮助，请帮助点亮一颗星。  



# 课程 4：更多工具 + 工具集组合

**目标：** 添加更多工具，并将它们分组为可启用/禁用的命名工具集。

**你将学到：**
- 如何向注册表添加新工具
- 工具集模式：命名的工具分组
- ToolsetManager 用于组合和解析工具集
- 按类别过滤工具（file_ops、code、web、all）

**新建文件：**
- `ultrabot/tools/toolsets.py` -- Toolset 数据类和 ToolsetManager

### 步骤 1：添加 PythonEvalTool

取自 `ultrabot/tools/builtin.py` 第 373-432 行：

```python
# 添加到 ultrabot/tools/builtin.py

class PythonEvalTool(Tool):
    """在子进程中执行 Python 代码片段。

    取自 ultrabot/tools/builtin.py 第 373-432 行。
    """

    name = "python_eval"
    description = (
        "Execute Python code in a sandboxed subprocess and return "
        "the captured stdout. Use for calculations, data processing, etc."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python source code to execute.",
            },
        },
        "required": ["code"],
    }

    async def execute(self, arguments: dict[str, Any]) -> str:
        import sys
        import textwrap

        code = arguments["code"]

        # 将用户代码包装起来，在子进程中捕获 stdout
        wrapper = textwrap.dedent("""\
            import sys, io
            _buf = io.StringIO()
            sys.stdout = _buf
            sys.stderr = _buf
            try:
                exec(compile({code!r}, "<python_eval>", "exec"))
            except Exception as _exc:
                print(f"Error: {{type(_exc).__name__}}: {{_exc}}")
            finally:
                sys.stdout = sys.__stdout__
                sys.stderr = sys.__stderr__
                print(_buf.getvalue(), end="")
        """).format(code=code)

        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", wrapper,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return "Error: Python execution timed out after 30s."

        output = stdout.decode(errors="replace") if stdout else ""
        return _truncate(output) if output.strip() else "(no output)"
```

更新 `register_builtin_tools` 以包含新工具：

```python
def register_builtin_tools(registry: ToolRegistry) -> None:
    for tool in [
        ReadFileTool(),
        WriteFileTool(),
        ListDirectoryTool(),
        ExecCommandTool(),
        WebSearchTool(),
        PythonEvalTool(),  # 新增
    ]:
        registry.register(tool)
```

### 步骤 2：创建工具集系统

这直接取自 `ultrabot/tools/toolsets.py`：

```python
# ultrabot/tools/toolsets.py
"""ultrabot 的工具集组合。

将工具分组为命名的集合，可以切换开/关并进行组合。

取自 ultrabot/tools/toolsets.py。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ultrabot.tools.base import Tool, ToolRegistry


@dataclass
class Toolset:
    """一组工具名称的命名分组。

    取自 ultrabot/tools/toolsets.py 第 23-44 行。
    """
    name: str
    description: str
    tool_names: list[str] = field(default_factory=list)
    enabled: bool = True


# 内置工具集定义（取自第 51-73 行）
TOOLSET_FILE_OPS = Toolset(
    "file_ops",
    "File read/write/list operations",
    ["read_file", "write_file", "list_directory"],
)

TOOLSET_CODE = Toolset(
    "code",
    "Code execution tools",
    ["exec_command", "python_eval"],
)

TOOLSET_WEB = Toolset(
    "web",
    "Web search and browsing",
    ["web_search"],
)

TOOLSET_ALL = Toolset(
    "all",
    "All available tools",
    [],  # 特殊：空列表解析为所有已注册的工具
)


class ToolsetManager:
    """管理命名的 Toolset 分组，并将它们解析为
    ToolRegistry 中的具体 Tool 实例。

    取自 ultrabot/tools/toolsets.py 第 81-187 行。
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry
        self._toolsets: dict[str, Toolset] = {}

    def register_toolset(self, toolset: Toolset) -> None:
        """注册或覆盖一个命名工具集。"""
        self._toolsets[toolset.name] = toolset

    def get_toolset(self, name: str) -> Toolset | None:
        return self._toolsets.get(name)

    def list_toolsets(self) -> list[Toolset]:
        return list(self._toolsets.values())

    def enable(self, name: str) -> None:
        """启用一个工具集。如果未注册则抛出 KeyError。"""
        ts = self._toolsets.get(name)
        if ts is None:
            raise KeyError(f"Unknown toolset: {name!r}")
        ts.enabled = True

    def disable(self, name: str) -> None:
        """禁用一个工具集。如果未注册则抛出 KeyError。"""
        ts = self._toolsets.get(name)
        if ts is None:
            raise KeyError(f"Unknown toolset: {name!r}")
        ts.enabled = False

    def resolve(self, toolset_names: list[str]) -> list[Tool]:
        """将工具集名称解析为扁平化、去重的 Tool 列表。

        'all' 工具集解析为注册表中的所有工具。
        只有已启用的工具集才会被考虑。
        """
        seen_names: set[str] = set()
        tools: list[Tool] = []

        for ts_name in toolset_names:
            ts = self._toolsets.get(ts_name)
            if ts is None or not ts.enabled:
                continue

            if not ts.tool_names:
                # 特殊的 "all" 语义
                for tool in self._registry.list_tools():
                    if tool.name not in seen_names:
                        seen_names.add(tool.name)
                        tools.append(tool)
            else:
                for tool_name in ts.tool_names:
                    if tool_name in seen_names:
                        continue
                    tool = self._registry.get(tool_name)
                    if tool is not None:
                        seen_names.add(tool_name)
                        tools.append(tool)

        return tools

    def get_definitions(self, toolset_names: list[str]) -> list[dict[str, Any]]:
        """返回已解析工具的 OpenAI 函数调用定义。"""
        return [tool.to_definition() for tool in self.resolve(toolset_names)]


def register_default_toolsets(manager: ToolsetManager) -> None:
    """注册内置工具集。

    取自 ultrabot/tools/toolsets.py 第 195-198 行。
    """
    for ts in (TOOLSET_FILE_OPS, TOOLSET_CODE, TOOLSET_WEB, TOOLSET_ALL):
        manager.register_toolset(ts)
```


### 步骤 3：从命令行使用工具集

更新 `main.py` 以接受 `--tools` 参数：

```python
# ultrabot/main.py -- 带工具集过滤
import os
import sys
from openai import OpenAI
from ultrabot.agent import Agent
from ultrabot.tools.base import ToolRegistry
from ultrabot.tools.builtin import register_builtin_tools
from ultrabot.tools.toolsets import ToolsetManager, register_default_toolsets

# 解析简单的 --tools 参数
toolset_arg = "all"
if "--tools" in sys.argv:
    idx = sys.argv.index("--tools")
    toolset_arg = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "all"
    
# 创建并填充工具注册表
registry = ToolRegistry()
register_builtin_tools(registry)

manager = ToolsetManager(registry)
register_default_toolsets(manager)

# 解析要使用哪些工具
active_tools = manager.resolve([toolset_arg])
print(f"Active tools: {', '.join(t.name for t in active_tools)}\n")

# 构建一个只包含活跃工具的过滤注册表
filtered_registry = ToolRegistry()
for tool in active_tools:
    filtered_registry.register(tool)

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL"),
)
model = os.getenv("MODEL")

agent = Agent(
    client=client,
    model=model,
    tool_registry=filtered_registry
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
# tests/test_session4.py
"""课程 4 的测试 -- 工具集。"""
import pytest
from ultrabot.tools.base import ToolRegistry
from ultrabot.tools.builtin import register_builtin_tools
from ultrabot.tools.toolsets import (
    Toolset,
    ToolsetManager,
    TOOLSET_FILE_OPS,
    TOOLSET_CODE,
    TOOLSET_WEB,
    TOOLSET_ALL,
    register_default_toolsets,
)


@pytest.fixture
def full_setup():
    """创建一个包含所有工具的注册表和包含所有工具集的管理器。"""
    registry = ToolRegistry()
    register_builtin_tools(registry)
    manager = ToolsetManager(registry)
    register_default_toolsets(manager)
    return registry, manager


def test_toolset_file_ops(full_setup):
    """file_ops 只解析为文件工具。"""
    _, manager = full_setup
    tools = manager.resolve(["file_ops"])
    names = {t.name for t in tools}
    assert names == {"read_file", "write_file", "list_directory"}


def test_toolset_code(full_setup):
    """code 解析为 exec 和 python_eval。"""
    _, manager = full_setup
    tools = manager.resolve(["code"])
    names = {t.name for t in tools}
    assert names == {"exec_command", "python_eval"}


def test_toolset_web(full_setup):
    """web 只解析为 web_search。"""
    _, manager = full_setup
    tools = manager.resolve(["web"])
    names = {t.name for t in tools}
    assert names == {"web_search"}


def test_toolset_all(full_setup):
    """all 解析为所有已注册的工具。"""
    registry, manager = full_setup
    tools = manager.resolve(["all"])
    assert len(tools) == len(registry)


def test_toolset_composition(full_setup):
    """多个工具集组合时不会重复。"""
    _, manager = full_setup
    tools = manager.resolve(["file_ops", "code"])
    names = [t.name for t in tools]
    assert len(names) == len(set(names))  # 无重复
    assert "read_file" in names
    assert "exec_command" in names


def test_toolset_disable(full_setup):
    """禁用的工具集在解析时被跳过。"""
    _, manager = full_setup
    manager.disable("web")
    tools = manager.resolve(["web"])
    assert len(tools) == 0

    manager.enable("web")
    tools = manager.resolve(["web"])
    assert len(tools) == 1


def test_unknown_toolset(full_setup):
    """未知的工具集名称被静默忽略。"""
    _, manager = full_setup
    tools = manager.resolve(["nonexistent"])
    assert len(tools) == 0
```

### 检查点

```bash
# 只使用代码工具
python ultrabot/main.py --tools code
```

```
Active tools: exec_command, python_eval

you > Calculate 2^100

assistant > [calls python_eval(code="print(2**100)")]
2^100 = 1,267,650,600,228,229,401,496,703,205,376
```

```bash
# 只使用文件工具
python ultrabot/main.py --tools file_ops
```

LLM 将只能看到文件工具，看不到 exec_command 或 web_search。

### 本课成果

一个将工具分组为命名类别的工具集系统。ToolsetManager 将工具集名称解析为具体的 Tool 实例，支持启用/禁用，并支持多个工具集的组合去重。这直接对应 `ultrabot/tools/toolsets.py`。

---

## 本课使用的 Python 知识

### `@dataclass` 与 `field(default_factory=list)`（数据类）

本课新增了 `Toolset` 数据类，用 `@dataclass` 自动生成 `__init__` 等方法：

```python
from dataclasses import dataclass, field

@dataclass
class Toolset:
    name: str
    description: str
    tool_names: list[str] = field(default_factory=list)
    enabled: bool = True
```

- 没有默认值的字段（`name`、`description`）是必填参数
- `field(default_factory=list)` 为每个实例创建独立的空列表
- `enabled: bool = True` 直接使用默认值

```python
ts = Toolset("file_ops", "File tools", ["read_file", "write_file"])
print(ts.name)        # file_ops
print(ts.enabled)     # True
```

**为什么在本课使用：** `Toolset` 只需要存储名称、描述、工具名列表和启用状态，没有复杂行为。`@dataclass` 让定义这种纯数据容器变得极其简洁。

---

### `set` 集合数据结构（去重）

集合（`set`）是一种**不允许重复元素**的无序数据结构：

```python
seen_names: set[str] = set()       # 创建空集合
seen_names.add("read_file")        # 添加元素
seen_names.add("read_file")        # 重复添加无效
print(len(seen_names))             # 1

# 集合推导式
names = {t.name for t in tools}    # 从列表快速创建集合
```

**为什么在本课使用：** 当多个工具集组合时（如 `["file_ops", "all"]`），同一个工具可能出现多次。`seen_names` 集合追踪已添加的工具名，确保结果列表中没有重复。测试中也用集合推导式 `{t.name for t in tools}` 来验证工具列表。

---

### `sys.argv`（命令行参数）

`sys.argv` 是一个列表，包含运行 Python 脚本时传入的命令行参数：

```python
import sys

# 运行命令：python main.py --tools code
print(sys.argv)
# ['main.py', '--tools', 'code']
# sys.argv[0] 是脚本名
# sys.argv[1] 是 '--tools'
# sys.argv[2] 是 'code'

if "--tools" in sys.argv:
    idx = sys.argv.index("--tools")
    toolset_arg = sys.argv[idx + 1]    # 获取 --tools 后面的值
```

**为什么在本课使用：** 让用户通过命令行选择启用哪些工具集（如 `--tools code` 只启用代码工具，`--tools file_ops` 只启用文件工具）。这是最简单的命令行参数解析方式，后续可以升级为 `argparse`。

---

### `@pytest.fixture`（pytest 夹具）

`@pytest.fixture` 定义可复用的测试准备逻辑，在多个测试函数之间共享：

```python
import pytest

@pytest.fixture
def full_setup():
    """创建包含所有工具的注册表和管理器。"""
    registry = ToolRegistry()
    register_builtin_tools(registry)
    manager = ToolsetManager(registry)
    register_default_toolsets(manager)
    return registry, manager

def test_toolset_file_ops(full_setup):   # full_setup 作为参数自动注入
    _, manager = full_setup
    tools = manager.resolve(["file_ops"])
    assert len(tools) == 3
```

pytest 在执行测试函数前，自动调用同名 fixture 并将返回值传入。每个测试函数都会获得一份全新的 fixture 实例。

**为什么在本课使用：** 本课有 7 个测试都需要相同的"注册表 + 管理器"初始化。`@pytest.fixture` 避免了在每个测试函数中重复编写相同的准备代码。

---

### `textwrap.dedent()`（去缩进）

`textwrap.dedent()` 移除多行字符串中公共的前导空白，让代码中的多行字符串保持美观的缩进：

```python
import textwrap

code = textwrap.dedent("""\
    import sys
    print("Hello")
    print(sys.version)
""")
# code 的每一行都去掉了前导的 4 个空格
```

**为什么在本课使用：** `PythonEvalTool` 需要生成一段 Python 包装代码。这段代码在源文件中是缩进的（在方法内部），但执行时需要去掉缩进。`dedent()` 让我们在保持源代码美观的同时，生成正确格式的代码。

---

### `sys.executable`（Python 解释器路径）

`sys.executable` 返回当前运行的 Python 解释器的完整路径：

```python
import sys
print(sys.executable)
# 例如: /home/user/.venv/bin/python3.12
```

**为什么在本课使用：** `PythonEvalTool` 需要在子进程中执行用户代码。使用 `sys.executable` 而不是硬编码 `"python"`，确保子进程使用的是与当前程序**同一个** Python 版本和虚拟环境，避免包找不到或版本不匹配的问题。

---

### 组合模式（`ToolsetManager` 组合 `ToolRegistry`）

组合模式是面向对象设计中"使用已有对象来构建更复杂对象"的思想，而不是通过继承：

```python
class ToolsetManager:
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry      # 组合：持有 registry 的引用
        self._toolsets: dict[str, Toolset] = {}

    def resolve(self, toolset_names: list[str]) -> list[Tool]:
        # 通过 registry 查找具体的 Tool 实例
        tool = self._registry.get(tool_name)
        ...
```

`ToolsetManager` 不继承 `ToolRegistry`，而是**持有**一个 `ToolRegistry` 实例并委托它来查找工具。

**为什么在本课使用：** `ToolsetManager` 负责"按名字分组管理工具集"，`ToolRegistry` 负责"存储和查找工具"。两者职责不同，用组合让它们各司其职、相互协作。如果用继承，会把两个不相关的职责混在一起。

---

### `f-string` 中的 `!r` 格式化（repr 表示）

`{value!r}` 在 f-string 中输出变量的 `repr()` 表示（带引号的字符串、完整的数据表示）：

```python
name = "hello"
print(f"Name is {name}")      # Name is hello
print(f"Name is {name!r}")    # Name is 'hello'（带引号）
```

在代码生成中特别有用：

```python
code = "print(42)"
wrapper = f"exec(compile({code!r}, '<eval>', 'exec'))"
# 结果: exec(compile("print(42)", '<eval>', 'exec'))
```

**为什么在本课使用：** `PythonEvalTool` 需要将用户的代码字符串嵌入到包装代码中。`{code!r}` 确保代码字符串被正确引用和转义，不会因为代码中包含引号或特殊字符而产生语法错误。

---

### `KeyError` 异常

`KeyError` 是当你访问字典中不存在的键时 Python 抛出的异常：

```python
data = {"a": 1}
try:
    print(data["b"])     # 键 "b" 不存在
except KeyError:
    print("Key not found!")
```

也可以主动抛出来表示"无效参数"：

```python
def enable(self, name: str) -> None:
    ts = self._toolsets.get(name)
    if ts is None:
        raise KeyError(f"Unknown toolset: {name!r}")
    ts.enabled = True
```

**为什么在本课使用：** `ToolsetManager.enable()` 和 `disable()` 在工具集名称不存在时主动抛出 `KeyError`，让调用者及时知道传入了无效名称。而 `resolve()` 方法则静默忽略未知名称——两种策略适用于不同场景。

---

### 函数内部导入

将 `import` 语句放在函数内部而不是文件顶部：

```python
class PythonEvalTool(Tool):
    async def execute(self, arguments):
        import sys           # 在函数内部导入
        import textwrap
        ...
```

**为什么在本课使用：** `sys` 和 `textwrap` 只在 `PythonEvalTool.execute()` 中使用。将导入放在函数内部可以延迟加载（只在该方法被调用时才导入），并让模块顶部的导入列表保持简洁。对于不常调用的工具，这种方式可以略微加快程序启动速度。
