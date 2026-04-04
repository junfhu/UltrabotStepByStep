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
