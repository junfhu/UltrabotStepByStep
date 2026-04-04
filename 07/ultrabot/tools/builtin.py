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


# ---- PythonEvalTool ----

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
        PythonEvalTool(),
    ]:
        registry.register(tool)
