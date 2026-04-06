# ultrabot/tools/toolsets.py
"""ultrabot 的工具集组合。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ultrabot.tools.base import Tool, ToolRegistry


@dataclass
class Toolset:
    """一组工具名称的命名分组。"""
    name: str
    description: str
    tool_names: list[str] = field(default_factory=list)
    enabled: bool = True


TOOLSET_FILE_OPS = Toolset("file_ops", "File read/write/list operations", ["read_file", "write_file", "list_directory"])
TOOLSET_CODE = Toolset("code", "Code execution tools", ["exec_command", "python_eval"])
TOOLSET_WEB = Toolset("web", "Web search and browsing", ["web_search"])
TOOLSET_ALL = Toolset("all", "All available tools", [])


class ToolsetManager:
    """管理命名的 Toolset 分组。"""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry
        self._toolsets: dict[str, Toolset] = {}

    def register_toolset(self, toolset: Toolset) -> None:
        self._toolsets[toolset.name] = toolset

    def get_toolset(self, name: str) -> Toolset | None:
        return self._toolsets.get(name)

    def list_toolsets(self) -> list[Toolset]:
        return list(self._toolsets.values())

    def enable(self, name: str) -> None:
        ts = self._toolsets.get(name)
        if ts is None:
            raise KeyError(f"Unknown toolset: {name!r}")
        ts.enabled = True

    def disable(self, name: str) -> None:
        ts = self._toolsets.get(name)
        if ts is None:
            raise KeyError(f"Unknown toolset: {name!r}")
        ts.enabled = False

    def resolve(self, toolset_names: list[str]) -> list[Tool]:
        seen_names: set[str] = set()
        tools: list[Tool] = []
        for ts_name in toolset_names:
            ts = self._toolsets.get(ts_name)
            if ts is None or not ts.enabled:
                continue
            if not ts.tool_names:
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
        return [tool.to_definition() for tool in self.resolve(toolset_names)]


def register_default_toolsets(manager: ToolsetManager) -> None:
    for ts in (TOOLSET_FILE_OPS, TOOLSET_CODE, TOOLSET_WEB, TOOLSET_ALL):
        manager.register_toolset(ts)
