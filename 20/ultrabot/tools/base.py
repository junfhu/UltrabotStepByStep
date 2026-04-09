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
