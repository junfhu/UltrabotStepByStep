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
