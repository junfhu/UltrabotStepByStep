# Ultrabot：30 课程开发指南
**从零开始构建一个生产级 AI 助手框架。**
本指南将带你从"向 LLM 问好"一步步走到一个完整的多提供者、多通道 AI 智能体，具备工具调用、记忆、安全防护和 Web 界面。每节课程都建立在上一节课的基础之上。每节课都包含可运行的代码和测试。  
本教程的主要思路来自于Nanobot(https://github.com/HKUDS/nanobot)以及Learn-Claude-Code(https://github.com/shareAI-lab/learn-claude-code/)，所以对应的叫做Ultrabot。  
本课程设计由AI辅助下完成，更新地址见https://github.com/junfhu/UltrabotStepByStep，如果您觉得对您有帮助，请帮助点亮一颗星。  
本课程中使用的大模型提供商是火山引擎Code Plan，如果正好你也需要，可以使用我的邀请码获取9折优惠 https://volcengine.com/L/_01BJCkKdMc/  邀请码：HHCDB4J4）  



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

from tools.base import Tool, ToolRegistry

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

现在是关键时刻 -- 我们更新 Agent 以支持工具调用。这是 `ultrabot/agent/agent.py` 第 99-174 行的核心逻辑：

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

    取自 ultrabot/agent/agent.py 第 24-30 行。
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

    对应 ultrabot/agent/agent.py -- run() 方法实现了
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

        循环（取自 ultrabot/agent/agent.py 第 110-174 行）：
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

        取自 ultrabot/agent/agent.py 第 180-233 行。
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
# main.py -- 带工具的智能体
import os
from openai import OpenAI
from agent import Agent
from tools.base import ToolRegistry
from tools.builtin import register_builtin_tools

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
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.base import Tool, ToolRegistry


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
    from tools.builtin import ReadFileTool

    test_file = tmp_path / "test.txt"
    test_file.write_text("Hello, world!")

    tool = ReadFileTool()
    result = asyncio.run(tool.execute({"path": str(test_file)}))
    assert "Hello, world!" in result


def test_list_directory_tool(tmp_path):
    """ListDirectoryTool 列出目录内容。"""
    from tools.builtin import ListDirectoryTool

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
    from tools.builtin import WriteFileTool

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
    from tools.builtin import register_builtin_tools

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
python main.py
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
