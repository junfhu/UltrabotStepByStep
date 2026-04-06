# ultrabot/agent/delegate.py
"""ultrabot 的子智能体委派。

允许父智能体生成一个具有受限工具集和独立对话上下文的
隔离子 Agent。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from ultrabot.agent.agent import Agent
from ultrabot.tools.base import Tool, ToolRegistry
from ultrabot.tools.toolsets import ToolsetManager


@dataclass
class DelegationRequest:
    """描述子智能体的子任务。"""
    task: str
    toolset_names: list[str] = field(default_factory=lambda: ["all"])
    max_iterations: int = 10
    timeout_seconds: float = 120.0
    context: str = ""


@dataclass
class DelegationResult:
    """子智能体运行的结果。"""
    task: str
    response: str
    success: bool
    iterations: int
    error: str = ""
    elapsed_seconds: float = 0.0


async def delegate(
    request: DelegationRequest,
    parent_config: Any,
    provider_manager: Any,
    tool_registry: ToolRegistry,
    toolset_manager: ToolsetManager | None = None,
) -> DelegationResult:
    """创建子 Agent 并隔离运行任务。"""
    start = time.monotonic()

    # 如果有工具集管理器，则构建受限注册表
    if toolset_manager is not None:
        resolved_tools = toolset_manager.resolve(request.toolset_names)
        child_registry = ToolRegistry()
        for tool in resolved_tools:
            child_registry.register(tool)
    else:
        child_registry = tool_registry

    # 轻量子配置，覆盖迭代限制
    child_config = _ChildConfig(parent_config, max_iterations=request.max_iterations)
    child_sessions = _InMemorySessionManager()

    child_agent = Agent(
        config=child_config,
        provider_manager=provider_manager,
        session_manager=child_sessions,
        tool_registry=child_registry,
    )

    user_message = request.task
    if request.context:
        user_message = f"CONTEXT:\n{request.context}\n\nTASK:\n{request.task}"

    session_key = "__delegate__"

    try:
        response = await asyncio.wait_for(
            child_agent.run(user_message=user_message, session_key=session_key),
            timeout=request.timeout_seconds,
        )
        elapsed = time.monotonic() - start
        iterations = _count_iterations(child_sessions, session_key)
        return DelegationResult(
            task=request.task, response=response, success=True,
            iterations=iterations, elapsed_seconds=round(elapsed, 3),
        )
    except asyncio.TimeoutError:
        elapsed = time.monotonic() - start
        return DelegationResult(
            task=request.task, response="", success=False, iterations=0,
            error=f"Delegation timed out after {request.timeout_seconds}s",
            elapsed_seconds=round(elapsed, 3),
        )
    except Exception as exc:
        elapsed = time.monotonic() - start
        return DelegationResult(
            task=request.task, response="", success=False, iterations=0,
            error=f"{type(exc).__name__}: {exc}",
            elapsed_seconds=round(elapsed, 3),
        )


class DelegateTaskTool(Tool):
    """将子任务委派给隔离子智能体的工具。"""
    name = "delegate_task"
    description = "Delegate a subtask to an isolated child agent with restricted tools"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "The subtask to accomplish."},
            "toolsets": {"type": "array", "items": {"type": "string"},
                         "description": 'Toolset names for the child (default: ["all"]).'},
            "max_iterations": {"type": "integer",
                               "description": "Max tool-call iterations (default 10)."},
        },
        "required": ["task"],
    }

    def __init__(self, parent_config, provider_manager, tool_registry, toolset_manager=None):
        self._parent_config = parent_config
        self._provider_manager = provider_manager
        self._tool_registry = tool_registry
        self._toolset_manager = toolset_manager

    async def execute(self, arguments: dict[str, Any]) -> str:
        task = arguments.get("task", "")
        if not task:
            return "Error: 'task' is required."

        request = DelegationRequest(
            task=task,
            toolset_names=arguments.get("toolsets") or ["all"],
            max_iterations=arguments.get("max_iterations", 10),
        )

        result = await delegate(
            request=request,
            parent_config=self._parent_config,
            provider_manager=self._provider_manager,
            tool_registry=self._tool_registry,
            toolset_manager=self._toolset_manager,
        )

        if result.success:
            return (f"[Delegation succeeded in {result.iterations} iteration(s), "
                    f"{result.elapsed_seconds}s]\n{result.response}")
        return f"[Delegation failed after {result.elapsed_seconds}s] {result.error}"


# ── 内部辅助类 ──────────────────────────────────────────────

class _ChildConfig:
    """覆盖 max_tool_iterations 的轻量包装器。"""
    def __init__(self, parent_config: Any, max_iterations: int = 10) -> None:
        self._parent = parent_config
        self.max_tool_iterations = max_iterations

    def __getattr__(self, name: str) -> Any:
        return getattr(self._parent, name)


class _InMemorySession:
    def __init__(self):
        self._messages: list[dict[str, Any]] = []

    def add_message(self, msg):
        self._messages.append(msg)

    def get_messages(self):
        return list(self._messages)

    def trim(self, max_tokens=128_000):
        pass


class _InMemorySessionManager:
    def __init__(self):
        self._sessions: dict[str, _InMemorySession] = {}

    async def get_or_create(self, key: str):
        if key not in self._sessions:
            self._sessions[key] = _InMemorySession()
        return self._sessions[key]

    def get_session(self, key: str):
        return self._sessions.get(key)


def _count_iterations(sm: _InMemorySessionManager, key: str) -> int:
    session = sm.get_session(key)
    if session is None:
        return 0
    return sum(1 for m in session.get_messages() if m.get("role") == "assistant")
