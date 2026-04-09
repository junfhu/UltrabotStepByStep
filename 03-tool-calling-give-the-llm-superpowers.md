# Agent: 30课程开发指南
**从零开始构建一个生产级 AI 助手框架。**
本指南将带你从"向 LLM 问好"一步步走到一个完整的多提供者、多通道 AI 智能体，具备工具调用、记忆、安全防护和 Web 界面。每节课程都建立在上一节课的基础之上。每节课都包含可运行的代码和测试。  
本教程的主要思路来自于
- Nanobot (https://github.com/HKUDS/nanobot)
- Learn-Claude-Code (https://github.com/shareAI-lab/learn-claude-code/)

本课程设计由AI辅助下完成，因为课程自身也在不停修正，请参考 https://github.com/junfhu/UltrabotStepByStep，如果您觉得对您有帮助，请帮助点亮一颗星。  



# 课程 3：工具调用 -- 赋予 LLM 超能力

**目标：** 让 LLM 调用函数（工具）来与真实世界交互 -- 读取文件、执行命令、搜索网页。

**你将学到：**
- LLM 函数调用 / 工具使用的工作原理
- Tool 抽象基类模式
- 用于管理工具的 ToolRegistry
- 如何将工具调用接入智能体循环

**新建文件：**
- `ultrabot/tools/base.py` -- Tool ABC 和 ToolRegistry
- `ultrabot/tools/builtin.py` -- 最初的 5 个内置工具

### 步骤 1：理解工具调用

当你给 LLM 一组工具定义（名称、描述、参数）时，它可以选择调用工具而不是用文本回复。流程如下：

```
用户："当前目录下有什么文件？"
  |
  v
LLM 看到工具：list_directory(path)
  |
  v
LLM 响应：tool_call(name="list_directory", arguments={"path": "."})
  |
  v
你的代码执行该工具，获取结果
  |
  v
你将结果以 "tool" 消息的形式发回给 LLM
  |
  v
LLM 阅读结果，组织自然语言回答
```

LLM 本身从不运行代码 -- 它只是请求*你*来运行，然后阅读输出。这个循环不断重复，直到 LLM 用文本回复（没有工具调用）。

### 步骤 2：创建 Tool 基类

这直接取自 `ultrabot/tools/base.py`：

```python
# ultrabot/tools/base.py
"""ultrabot 工具系统的基类。"""
from __future__ import annotations

import abc
from typing import Any


class Tool(abc.ABC):
    """所有工具的抽象基类。

    每个工具必须声明一个 *name*（名称）、一个人类可读的 *description*（描述）、
    以及一个遵循 OpenAI 函数调用 API 所使用的 JSON-Schema 规范的
    *parameters*（参数）字典。

    取自 ultrabot/tools/base.py 第 11-43 行。
    """

    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {}

    @abc.abstractmethod
    async def execute(self, arguments: dict[str, Any]) -> str:
        """使用给定参数运行工具并返回结果字符串。"""

    def to_definition(self) -> dict[str, Any]:
        """返回 OpenAI 函数调用工具定义。

        这就是发送给 LLM 的内容，让它知道有哪些工具可用
        以及接受什么参数。
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """按名称持有 Tool 实例的注册表，以 OpenAI 函数调用格式
    暴露它们。

    取自 ultrabot/tools/base.py 第 46-103 行。
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """注册一个工具。如果已存在同名工具则覆盖。"""
        if not tool.name:
            raise ValueError("Tool must have a non-empty 'name' attribute.")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        """返回具有给定名称的工具，如果不存在则返回 None。"""
        return self._tools.get(name)

    def list_tools(self) -> list[Tool]:
        """返回所有已注册的工具。"""
        return list(self._tools.values())

    def get_definitions(self) -> list[dict[str, Any]]:
        """返回所有已注册工具的 OpenAI 函数调用定义。"""
        return [tool.to_definition() for tool in self._tools.values()]

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
```

### 步骤 3：构建最初的 5 个工具

这些是 `ultrabot/tools/builtin.py` 中工具的简化版本：

```python
# ultrabot/tools/builtin.py
"""ultrabot 内置工具。

为教学目的简化自 ultrabot/tools/builtin.py。
"""
from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path
from typing import Any

from ultrabot.tools.base import Tool, ToolRegistry

_MAX_OUTPUT_CHARS = 80_000  # 硬性上限，避免撑爆 LLM 上下文窗口


def _truncate(text: str, limit: int = _MAX_OUTPUT_CHARS) -> str:
    """截断过长输出以适应 LLM 上下文窗口。"""
    if len(text) <= limit:
        return text
    half = limit // 2
    return (
        text[:half]
        + f"\n\n... [truncated {len(text) - limit} chars] ...\n\n"
        + text[-half:]
    )


# ---- ReadFileTool ----

class ReadFileTool(Tool):
    """读取磁盘上的文件内容。

    取自 ultrabot/tools/builtin.py 第 122-180 行。
    """

    name = "read_file"
    description = (
        "Read the contents of a file. Optionally specify offset and limit "
        "to read only a slice."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file to read.",
            },
            "offset": {
                "type": "integer",
                "description": "1-based line number to start from (optional).",
            },
            "limit": {
                "type": "integer",
                "description": "Max number of lines to read (optional).",
            },
        },
        "required": ["path"],
    }

    async def execute(self, arguments: dict[str, Any]) -> str:
        fpath = Path(arguments["path"]).expanduser().resolve()
        if not fpath.exists():
            return f"Error: file not found: {fpath}"
        if not fpath.is_file():
            return f"Error: not a regular file: {fpath}"

        text = fpath.read_text(errors="replace")

        offset = arguments.get("offset")
        limit = arguments.get("limit")
        if offset is not None or limit is not None:
            lines = text.splitlines(keepends=True)
            start = max((offset or 1) - 1, 0)
            end = start + limit if limit else len(lines)
            text = "".join(lines[start:end])

        return _truncate(text)


# ---- WriteFileTool ----

class WriteFileTool(Tool):
    """将内容写入文件，必要时创建父目录。

    取自 ultrabot/tools/builtin.py 第 188-228 行。
    """

    name = "write_file"
    description = "Write content to a file, creating parent directories if needed."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file to write.",
            },
            "content": {
                "type": "string",
                "description": "The full content to write.",
            },
        },
        "required": ["path", "content"],
    }

    async def execute(self, arguments: dict[str, Any]) -> str:
        fpath = Path(arguments["path"]).expanduser().resolve()
        content = arguments["content"]
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(content)
        return f"Successfully wrote {len(content)} characters to {fpath}"


# ---- ListDirectoryTool ----

class ListDirectoryTool(Tool):
    """列出目录中的条目。

    取自 ultrabot/tools/builtin.py 第 236-298 行。
    """

    name = "list_directory"
    description = (
        "List files and subdirectories in the given path. "
        "Returns name, type, and size for each entry."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory path to list.",
            },
        },
        "required": ["path"],
    }

    async def execute(self, arguments: dict[str, Any]) -> str:
        dirpath = Path(arguments["path"]).expanduser().resolve()
        if not dirpath.exists():
            return f"Error: directory not found: {dirpath}"
        if not dirpath.is_dir():
            return f"Error: not a directory: {dirpath}"

        entries = sorted(
            dirpath.iterdir(),
            key=lambda p: (not p.is_dir(), p.name.lower()),
        )
        if not entries:
            return f"Directory is empty: {dirpath}"

        lines = [f"Contents of {dirpath} ({len(entries)} entries):", ""]
        for entry in entries:
            try:
                st = entry.stat()
                kind = "DIR " if stat.S_ISDIR(st.st_mode) else "FILE"
                size = f"  {st.st_size:,} bytes" if kind == "FILE" else ""
                lines.append(f"  {kind}  {entry.name}{size}")
            except OSError:
                lines.append(f"  ???   {entry.name}")
        return "\n".join(lines)


# ---- ExecCommandTool ----

class ExecCommandTool(Tool):
    """执行 shell 命令并返回输出。

    取自 ultrabot/tools/builtin.py 第 306-365 行。
    """

    name = "exec_command"
    description = (
        "Run a shell command and return stdout + stderr. "
        "Use for system operations, builds, git, etc."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute.",
            },
            "timeout": {
                "type": "integer",
                "description": "Max execution time in seconds (default 60).",
                "default": 60,
            },
        },
        "required": ["command"],
    }

    async def execute(self, arguments: dict[str, Any]) -> str:
        command = arguments["command"]
        timeout = int(arguments.get("timeout", 60))

        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return f"Error: command timed out after {timeout}s."

        output = stdout.decode(errors="replace") if stdout else ""
        return _truncate(output) + f"\n[exit code: {proc.returncode}]"


# ---- WebSearchTool ----

class WebSearchTool(Tool):
    """通过 DuckDuckGo 搜索网络。

    取自 ultrabot/tools/builtin.py 第 60-114 行。
    """

    name = "web_search"
    description = (
        "Search the web using DuckDuckGo. Use when you need current "
        "information not in your training data."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query.",
            },
            "max_results": {
                "type": "integer",
                "description": "Max results to return (default 5).",
                "default": 5,
            },
        },
        "required": ["query"],
    }

    async def execute(self, arguments: dict[str, Any]) -> str:
        query = arguments["query"]
        max_results = int(arguments.get("max_results", 5))

        try:
            from ddgs import DDGS
        except ImportError:
            return "Error: 'ddgs' not installed. Run: pip install ddgs"

        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(
            None, lambda: list(DDGS().text(query, max_results=max_results))
        )

        if not results:
            return "No results found."

        lines = []
        for idx, r in enumerate(results, 1):
            title = r.get("title", "")
            href = r.get("href", r.get("link", ""))
            body = r.get("body", r.get("snippet", ""))
            lines.append(f"[{idx}] {title}\n    URL: {href}\n    {body}")
        return "\n\n".join(lines)


# ---- 注册辅助函数 ----

def register_builtin_tools(registry: ToolRegistry) -> None:
    """注册所有内置工具。

    取自 ultrabot/tools/builtin.py 第 440-475 行。
    """
    for tool in [
        ReadFileTool(),
        WriteFileTool(),
        ListDirectoryTool(),
        ExecCommandTool(),
        WebSearchTool(),
    ]:
        registry.register(tool)
```

### 步骤 4：将工具接入智能体循环

现在是关键时刻 -- 我们更新 Agent 以支持工具调用。这是 `ultrabot/agent.py` 第 99-174 行的核心逻辑：

```python
from __future__ import annotations
import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Callable

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
```

### 步骤 5：整合使用

```python
# ultrabot/main.py -- 带工具的智能体
import os
from openai import OpenAI
from ultrabot.agent import Agent
from ultrabot.tools.base import ToolRegistry
from ultrabot.tools.builtin import register_builtin_tools

# 创建并填充工具注册表
registry = ToolRegistry()
register_builtin_tools(registry)

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL"),
)
model = os.getenv("MODEL")

agent = Agent(
    client=client,
    model=model,
    tool_registry=registry
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
# tests/test_session3.py
"""课程 3 的测试 -- 工具调用。"""
import asyncio
from pathlib import Path

from ultrabot.tools.base import Tool, ToolRegistry


class EchoTool(Tool):
    """一个简单的测试工具，回显输入内容。"""
    name = "echo"
    description = "Echo the input text."
    parameters = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text to echo."},
        },
        "required": ["text"],
    }

    async def execute(self, arguments):
        return f"Echo: {arguments['text']}"


def test_tool_definition():
    """Tool.to_definition() 生成有效的 OpenAI 格式。"""
    tool = EchoTool()
    defn = tool.to_definition()

    assert defn["type"] == "function"
    assert defn["function"]["name"] == "echo"
    assert "parameters" in defn["function"]
    assert defn["function"]["parameters"]["required"] == ["text"]


def test_tool_registry():
    """ToolRegistry 存储和检索工具。"""
    registry = ToolRegistry()
    tool = EchoTool()

    registry.register(tool)
    assert "echo" in registry
    assert len(registry) == 1
    assert registry.get("echo") is tool
    assert registry.get("nonexistent") is None


def test_tool_registry_definitions():
    """get_definitions() 返回 OpenAI 格式的列表。"""
    registry = ToolRegistry()
    registry.register(EchoTool())
    defs = registry.get_definitions()

    assert len(defs) == 1
    assert defs[0]["function"]["name"] == "echo"


def test_tool_execute():
    """Tool.execute() 返回预期结果。"""
    tool = EchoTool()
    result = asyncio.run(tool.execute({"text": "hello"}))
    assert result == "Echo: hello"


def test_read_file_tool(tmp_path):
    """ReadFileTool 读取文件内容。"""
    from ultrabot.tools.builtin import ReadFileTool

    test_file = tmp_path / "test.txt"
    test_file.write_text("Hello, world!")

    tool = ReadFileTool()
    result = asyncio.run(tool.execute({"path": str(test_file)}))
    assert "Hello, world!" in result


def test_list_directory_tool(tmp_path):
    """ListDirectoryTool 列出目录内容。"""
    from ultrabot.tools.builtin import ListDirectoryTool

    (tmp_path / "file_a.txt").write_text("a")
    (tmp_path / "file_b.txt").write_text("b")
    (tmp_path / "subdir").mkdir()

    tool = ListDirectoryTool()
    result = asyncio.run(tool.execute({"path": str(tmp_path)}))
    assert "file_a.txt" in result
    assert "file_b.txt" in result
    assert "subdir" in result


def test_write_file_tool(tmp_path):
    """WriteFileTool 创建并写入文件。"""
    from ultrabot.tools.builtin import WriteFileTool

    target = tmp_path / "output" / "test.txt"
    tool = WriteFileTool()
    result = asyncio.run(tool.execute({
        "path": str(target),
        "content": "Written by tool!",
    }))
    assert "Successfully wrote" in result
    assert target.read_text() == "Written by tool!"


def test_builtin_registration():
    """register_builtin_tools 填充注册表。"""
    from ultrabot.tools.builtin import register_builtin_tools

    registry = ToolRegistry()
    register_builtin_tools(registry)

    assert len(registry) == 5
    assert "read_file" in registry
    assert "write_file" in registry
    assert "list_directory" in registry
    assert "exec_command" in registry
    assert "web_search" in registry
```

### 检查点

```bash
python ultrabot/main.py
```

```
you > What files are in the current directory?

assistant > Let me check...
[calls list_directory(path=".")]
Here are the files in the current directory:
  DIR   ultrabot
  DIR   tests
  FILE  chat.py  234 bytes
  FILE  main.py  487 bytes
  FILE  pyproject.toml  198 bytes
```

LLM 现在可以读取文件、列出目录和执行命令了。

### 本课成果

一个包含 ABC（`Tool`）、注册表（`ToolRegistry`）和 5 个内置工具的工具系统。智能体循环现在处理完整的工具调用流程：LLM 请求一个工具 -> 我们执行它 -> 将结果发回 -> LLM 组织自然语言回答。

---

## 本课使用的 Python 知识

### `abc.ABC` 和 `@abc.abstractmethod`（抽象基类）

抽象基类（Abstract Base Class）定义了一个"契约"——子类**必须**实现被 `@abstractmethod` 标记的方法，否则无法被实例化：

```python
import abc

class Tool(abc.ABC):
    @abc.abstractmethod
    async def execute(self, arguments: dict) -> str:
        """子类必须实现此方法。"""

class ReadFileTool(Tool):
    async def execute(self, arguments: dict) -> str:
        return "file content..."    # 实现了抽象方法，可以实例化

# tool = Tool()          # 报错！不能直接实例化抽象类
tool = ReadFileTool()     # 正确！子类实现了所有抽象方法
```

**为什么在本课使用：** `Tool` 基类定义了所有工具必须遵循的接口——每个工具都必须有 `name`、`description`、`parameters` 属性和 `execute()` 方法。这样无论是 `ReadFileTool` 还是 `WebSearchTool`，Agent 都可以用同样的方式调用它们。

---

### `async def` / `await`（异步编程）

`async def` 定义一个**协程函数**，`await` 用于等待异步操作完成：

```python
import asyncio

async def fetch_data():
    await asyncio.sleep(1)     # 异步等待 1 秒（不阻塞其他任务）
    return "data"

# 运行协程
result = asyncio.run(fetch_data())
```

与普通函数的区别：异步函数在等待 I/O（网络请求、文件读写、子进程）时，可以让出控制权给其他任务，而不是空等。

**为什么在本课使用：** 工具执行涉及文件 I/O 和子进程调用，这些都是耗时操作。用 `async` 定义 `execute()` 方法，未来可以并发执行多个工具调用（如同时读文件和搜索网络），而不必一个接一个等待。

---

### `asyncio.run()`（运行协程的入口）

`asyncio.run()` 是在普通（同步）代码中执行协程的标准方式：

```python
import asyncio

async def greet():
    return "Hello!"

# 在同步代码中调用异步函数
result = asyncio.run(greet())
print(result)   # Hello!
```

**为什么在本课使用：** Agent 的 `run()` 方法目前是同步的，但工具的 `execute()` 方法是异步的。`asyncio.run()` 在同步上下文中"桥接"异步代码，让我们能调用 `await tool.execute()`。

---

### `asyncio.create_subprocess_shell()`（异步子进程）

用异步方式启动一个 shell 命令，不阻塞事件循环：

```python
proc = await asyncio.create_subprocess_shell(
    "ls -la",
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.STDOUT,
)
stdout, _ = await proc.communicate()
print(stdout.decode())
```

**为什么在本课使用：** `ExecCommandTool` 需要执行用户请求的 shell 命令（如 `git status`、`ls`）。用异步子进程可以在等待命令执行时让出控制权，未来支持并发执行多个命令。

---

### `asyncio.wait_for()` 和 `asyncio.TimeoutError`（异步超时）

`wait_for()` 给异步操作设定最大等待时间，超时则抛出 `TimeoutError`：

```python
try:
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
except asyncio.TimeoutError:
    proc.kill()
    await proc.wait()
    return "命令超时了！"
```

**为什么在本课使用：** 用户可能让 LLM 执行一个永远不会结束的命令（比如 `sleep 999999`）。超时保护确保命令在指定时间内完成，否则强制终止，防止系统资源被耗尽。

---

### `asyncio.get_running_loop()` 和 `loop.run_in_executor()`（在线程池中运行同步代码）

有些第三方库不支持 `async`，需要在单独的线程中运行以避免阻塞事件循环：

```python
loop = asyncio.get_running_loop()
results = await loop.run_in_executor(
    None,    # 使用默认线程池
    lambda: list(DDGS().text(query, max_results=5))
)
```

**为什么在本课使用：** `WebSearchTool` 使用的 `ddgs` 库是同步的。如果直接在异步函数中调用，会阻塞整个事件循环。`run_in_executor()` 把同步调用放到线程池中，既不阻塞又能获取结果。

---

### `pathlib.Path`（路径操作）

`pathlib.Path` 是 Python 3.4+ 引入的面向对象路径操作方式，比 `os.path` 更直观：

```python
from pathlib import Path

fpath = Path("~/documents/test.txt")
fpath = fpath.expanduser()    # 展开 ~ 为用户主目录
fpath = fpath.resolve()       # 转为绝对路径

if fpath.exists() and fpath.is_file():
    text = fpath.read_text()  # 读取文件内容

# 创建父目录并写入
fpath.parent.mkdir(parents=True, exist_ok=True)
fpath.write_text("Hello!")
```

**为什么在本课使用：** 文件操作工具（`ReadFileTool`、`WriteFileTool`、`ListDirectoryTool`）需要处理路径解析、存在性检查、目录创建等。`pathlib.Path` 提供了链式调用的简洁 API。

---

### `json.loads()` 和 `json.dumps()`（JSON 序列化）

`json.loads()` 将 JSON 字符串解析为 Python 对象，`json.dumps()` 做相反操作：

```python
import json

# JSON 字符串 -> Python 字典
data = json.loads('{"name": "test", "args": {"path": "."}}')
print(data["name"])   # test

# Python 字典 -> JSON 字符串
text = json.dumps({"name": "test"})
print(text)           # {"name": "test"}
```

**为什么在本课使用：** LLM 返回的工具调用参数是 JSON 字符串格式（如 `'{"path": "."}'`），需要用 `json.loads()` 解析为 Python 字典才能使用。构建发送给 LLM 的工具调用消息时，又需要 `json.dumps()` 将字典转回 JSON 字符串。

---

### 类继承

子类继承父类的属性和方法，并可以覆盖或扩展它们：

```python
class Tool(abc.ABC):          # 父类（基类）
    name: str = ""
    description: str = ""

    @abc.abstractmethod
    async def execute(self, arguments):
        ...

class ReadFileTool(Tool):      # 子类继承 Tool
    name = "read_file"         # 覆盖父类属性
    description = "Read file"

    async def execute(self, arguments):   # 实现抽象方法
        return Path(arguments["path"]).read_text()
```

**为什么在本课使用：** 所有工具都继承自 `Tool` 基类，共享 `to_definition()` 方法（将工具信息转为 OpenAI 格式）。每个具体工具只需定义自己的 `name`、`description`、`parameters` 和 `execute()` 实现。这避免了大量重复代码。

---

### `dict.get()` 字典安全访问

`dict.get(key, default)` 在键不存在时返回默认值，而不是抛出 `KeyError`：

```python
data = {"name": "test"}
print(data.get("name"))           # "test"
print(data.get("missing"))        # None
print(data.get("missing", 60))    # 60
```

**为什么在本课使用：** 工具参数中，`offset`、`limit`、`timeout` 等是可选的。用 `arguments.get("timeout", 60)` 在参数缺失时安全地使用默认值，不会因为用户没传某个参数就报错。

---

### `enumerate()` 枚举

`enumerate()` 在遍历时同时获取索引和元素：

```python
fruits = ["apple", "banana", "cherry"]
for idx, fruit in enumerate(fruits, 1):   # 从 1 开始编号
    print(f"[{idx}] {fruit}")
# [1] apple
# [2] banana
# [3] cherry
```

**为什么在本课使用：** `WebSearchTool` 需要为搜索结果编号（`[1]`、`[2]`、`[3]`...），`enumerate(results, 1)` 让我们同时获取序号和结果内容。

---

### `sorted()` + `key=lambda` 自定义排序

`sorted()` 返回排序后的新列表，`key` 参数指定排序依据：

```python
entries = [Path("b.txt"), Path("subdir"), Path("a.txt")]

sorted_entries = sorted(
    entries,
    key=lambda p: (not p.is_dir(), p.name.lower()),
)
# 结果：先目录，再文件，各自按名称排序
```

`key=lambda` 返回一个元组用于多级排序：先按是否是目录排（`False < True`，所以目录排前面），再按小写名称排。

**为什么在本课使用：** `ListDirectoryTool` 需要将目录条目按"目录优先、文件在后"的方式排列，并按名称字母排序，提供整洁的输出。

---

### `**kwargs` 关键字参数解包

`**` 将字典解包为函数的关键字参数：

```python
kwargs = {
    "model": "gpt-4o",
    "messages": [...],
    "stream": True,
}
# 等价于 client.chat.completions.create(model="gpt-4o", messages=[...], stream=True)
response = client.chat.completions.create(**kwargs)
```

**为什么在本课使用：** `_chat_stream` 方法根据是否有工具定义来动态构建 API 参数。先把必要参数放进字典，有工具时再添加 `tools` 键，最后用 `**kwargs` 一次性传给 API。这比写一堆 `if-else` 更简洁。

---

### 列表推导式

列表推导式是用一行代码从现有序列创建新列表的简洁方式：

```python
# 传统写法
definitions = []
for tool in tools:
    definitions.append(tool.to_definition())

# 列表推导式写法
definitions = [tool.to_definition() for tool in tools]
```

**为什么在本课使用：** `ToolRegistry.get_definitions()` 需要将所有工具转为 API 定义格式。列表推导式让这个转换操作只需一行代码，既简洁又符合 Python 惯用风格。

---

### `try` / `except` / `Exception` 异常处理

`try/except` 捕获并处理运行时错误，程序不会崩溃：

```python
try:
    result = await tool.execute(arguments)
except Exception as exc:
    result = f"Error: {type(exc).__name__}: {exc}"
```

`type(exc).__name__` 获取异常类的名称（如 `"FileNotFoundError"`），`exc` 是异常实例（包含错误详情）。

**为什么在本课使用：** 工具执行可能因各种原因失败（文件不存在、命令出错、网络中断）。`try/except` 确保单个工具失败时不会让整个智能体崩溃，而是将错误信息返回给 LLM，让它决定下一步。

---

### 数字下划线分隔符

Python 3.6+ 允许在数字中使用下划线增加可读性，不影响值：

```python
_MAX_OUTPUT_CHARS = 80_000    # 等同于 80000
budget = 1_000_000            # 等同于 1000000
```

**为什么在本课使用：** `80_000` 比 `80000` 更容易一眼看出是"八万"，在定义最大字符数限制时提高了代码可读性。

---

### 条件导入（延迟导入）

在函数内部而非文件顶部导入模块，通常用于可选依赖：

```python
async def execute(self, arguments):
    try:
        from ddgs import DDGS           # 仅在调用时才导入
    except ImportError:
        return "Error: 'ddgs' not installed. Run: pip install ddgs"
    ...
```

**为什么在本课使用：** `ddgs`（DuckDuckGo 搜索库）不是必须安装的依赖。将 `import` 放在函数内部，只有真正使用 `WebSearchTool` 时才会导入。如果没安装，会给出友好的错误提示而不是让整个程序无法启动。

---

### `stat` 模块（文件状态检查）

`stat` 模块提供检查文件类型和权限的工具函数：

```python
import stat

st = entry.stat()                         # 获取文件状态信息
is_dir = stat.S_ISDIR(st.st_mode)         # 是否是目录
size = st.st_size                          # 文件大小（字节）
```

**为什么在本课使用：** `ListDirectoryTool` 需要区分目录和文件，并显示文件大小。`stat.S_ISDIR()` 通过检查文件的 mode 位来判断类型，比 `Path.is_dir()` 更高效（只需一次系统调用）。
