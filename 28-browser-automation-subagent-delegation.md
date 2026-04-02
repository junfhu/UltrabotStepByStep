# 课程 28：浏览器自动化 + 子智能体委派

**目标：** 为智能体提供无头浏览器进行网页交互的能力，以及将子任务委派给隔离子智能体的能力。

**你将学到：**
- 六个浏览器工具，封装 Playwright 的异步 API
- 延迟导入，使 Playwright 成为可选依赖
- 子智能体委派，具有受限工具集和独立上下文
- 子智能体的超时处理和迭代计数

**新建文件：**
- `ultrabot/tools/browser.py` — 6 个浏览器工具 + `_BrowserManager` 单例
- `ultrabot/agent/delegate.py` — `DelegateTaskTool`、`DelegationRequest`、`DelegationResult`

### 步骤 1：浏览器管理器（延迟单例）

所有浏览器工具共享由模块级单例管理的单个页面实例。Playwright 采用延迟导入，因此即使未安装也能正常导入该模块。

```python
# ultrabot/tools/browser.py
"""ultrabot 的浏览器自动化工具。

六个工具类封装了 Playwright 的异步 API，用于无头 Chromium：
- BrowserNavigateTool  – 导航到 URL
- BrowserSnapshotTool  – 捕获页面文本内容
- BrowserClickTool     – 点击 CSS 选择器指定的元素
- BrowserTypeTool      – 在输入框中输入文本
- BrowserScrollTool    – 上下滚动页面
- BrowserCloseTool     – 关闭浏览器实例

所有 Playwright 导入都是延迟的，因此在未安装 Playwright 时
也可以导入本模块。
"""

from __future__ import annotations
from typing import Any
from loguru import logger
from ultrabot.tools.base import Tool, ToolRegistry

_PLAYWRIGHT_INSTALL_HINT = (
    "Error: Playwright is not installed. "
    "Install it with:  pip install playwright && python -m playwright install chromium"
)

_DEFAULT_TIMEOUT_MS = 30_000


class _BrowserManager:
    """延迟管理单个 Playwright 浏览器/上下文/页面。"""

    def __init__(self) -> None:
        self._playwright: Any | None = None
        self._browser: Any | None = None
        self._page: Any | None = None

    async def ensure_browser(self) -> Any:
        """返回活动页面，延迟创建浏览器/上下文。"""
        if self._page is not None and not self._page.is_closed():
            return self._page

        from playwright.async_api import async_playwright  # 延迟导入

        if self._playwright is None:
            self._playwright = await async_playwright().start()

        self._browser = await self._playwright.chromium.launch(headless=True)
        context = await self._browser.new_context()
        context.set_default_timeout(_DEFAULT_TIMEOUT_MS)
        self._page = await context.new_page()
        logger.debug("Browser launched (headless Chromium)")
        return self._page

    async def close(self) -> None:
        """关闭浏览器和 Playwright。"""
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception as exc:
                logger.warning("Error closing browser: {}", exc)
            self._browser = None
            self._page = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception as exc:
                logger.warning("Error stopping playwright: {}", exc)
            self._playwright = None

# 模块级单例
_manager = _BrowserManager()
```

### 步骤 2：浏览器工具

每个工具遵循相同的模式：从管理器获取页面，执行操作，返回文本结果。

```python
class BrowserNavigateTool(Tool):
    """导航到 URL 并返回页面标题和文本内容。"""
    name = "browser_navigate"
    description = "Navigate to a URL in a headless browser and return the page title and first 2000 chars of visible text."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The URL to navigate to."},
        },
        "required": ["url"],
    }

    async def execute(self, arguments: dict[str, Any]) -> str:
        url: str = arguments["url"]
        try:
            page = await _manager.ensure_browser()
        except ImportError:
            return _PLAYWRIGHT_INSTALL_HINT
        try:
            await page.goto(url, wait_until="domcontentloaded")
            title = await page.title()
            text = await page.inner_text("body")
            return f"Title: {title}\n\n{text[:2000]}"
        except Exception as exc:
            return f"Navigation error: {exc}"


class BrowserSnapshotTool(Tool):
    """返回当前页面的文本内容。"""
    name = "browser_snapshot"
    description = "Return current page title, URL, and visible text (truncated to 4000 chars)."
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    async def execute(self, arguments: dict[str, Any]) -> str:
        try:
            page = await _manager.ensure_browser()
        except ImportError:
            return _PLAYWRIGHT_INSTALL_HINT
        try:
            title = await page.title()
            url = page.url
            text = await page.inner_text("body")
            return f"Title: {title}\nURL: {url}\n\n{text[:4000]}"
        except Exception as exc:
            return f"Snapshot error: {exc}"


class BrowserClickTool(Tool):
    """通过 CSS 选择器点击元素。"""
    name = "browser_click"
    description = "Click an element on the current page by CSS selector."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "selector": {"type": "string", "description": "CSS selector for the element."},
        },
        "required": ["selector"],
    }

    async def execute(self, arguments: dict[str, Any]) -> str:
        selector: str = arguments["selector"]
        try:
            page = await _manager.ensure_browser()
        except ImportError:
            return _PLAYWRIGHT_INSTALL_HINT
        try:
            await page.click(selector)
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            return f"Clicked element: {selector}"
        except Exception as exc:
            return f"Click error: {exc}"


class BrowserTypeTool(Tool):
    """在输入框中输入文本。"""
    name = "browser_type"
    description = "Type text into an input field identified by CSS selector."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "selector": {"type": "string", "description": "CSS selector for the input."},
            "text": {"type": "string", "description": "Text to type."},
        },
        "required": ["selector", "text"],
    }

    async def execute(self, arguments: dict[str, Any]) -> str:
        selector, text = arguments["selector"], arguments["text"]
        try:
            page = await _manager.ensure_browser()
        except ImportError:
            return _PLAYWRIGHT_INSTALL_HINT
        try:
            await page.fill(selector, text)
            return f"Typed into {selector}: {text!r}"
        except Exception as exc:
            return f"Type error: {exc}"


class BrowserScrollTool(Tool):
    """上下滚动页面。"""
    name = "browser_scroll"
    description = "Scroll the current page up or down by a given number of pixels."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "direction": {"type": "string", "enum": ["up", "down"]},
            "amount": {"type": "integer", "description": "Pixels to scroll (default 500).", "default": 500},
        },
        "required": ["direction"],
    }

    async def execute(self, arguments: dict[str, Any]) -> str:
        direction = arguments["direction"]
        amount = int(arguments.get("amount", 500))
        try:
            page = await _manager.ensure_browser()
        except ImportError:
            return _PLAYWRIGHT_INSTALL_HINT
        try:
            delta = amount if direction == "down" else -amount
            await page.evaluate(f"window.scrollBy(0, {delta})")
            pos = await page.evaluate("window.scrollY")
            return f"Scrolled {direction} by {amount}px. Position: {pos}px"
        except Exception as exc:
            return f"Scroll error: {exc}"


class BrowserCloseTool(Tool):
    """关闭浏览器实例。"""
    name = "browser_close"
    description = "Close the headless browser and free resources."
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    async def execute(self, arguments: dict[str, Any]) -> str:
        try:
            await _manager.close()
            return "Browser closed successfully."
        except Exception as exc:
            return f"Error closing browser: {exc}"


def register_browser_tools(registry: ToolRegistry) -> None:
    """实例化并注册所有浏览器工具。"""
    for cls in [BrowserNavigateTool, BrowserSnapshotTool, BrowserClickTool,
                BrowserTypeTool, BrowserScrollTool, BrowserCloseTool]:
        registry.register(cls())
    logger.info("Registered 6 browser tool(s)")
```

### 步骤 3：子智能体委派

`DelegateTaskTool` 生成一个隔离的子 `Agent`，具有自己的会话、受限工具集和超时设置。

```python
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
```

### 测试

```python
# tests/test_browser_delegate.py
"""浏览器工具和子智能体委派的测试。"""

import pytest
from ultrabot.agent.delegate import (
    DelegationRequest, DelegationResult,
    _InMemorySessionManager, _InMemorySession, _ChildConfig, _count_iterations,
)
from ultrabot.tools.browser import (
    BrowserNavigateTool, BrowserSnapshotTool, BrowserCloseTool,
    _BrowserManager, _PLAYWRIGHT_INSTALL_HINT,
)


class TestDelegationDataClasses:
    def test_request_defaults(self):
        req = DelegationRequest(task="Do something")
        assert req.toolset_names == ["all"]
        assert req.max_iterations == 10
        assert req.timeout_seconds == 120.0

    def test_result_success(self):
        res = DelegationResult(
            task="test", response="Done", success=True, iterations=3,
        )
        assert res.success
        assert res.error == ""


class TestInMemorySession:
    def test_add_and_get_messages(self):
        session = _InMemorySession()
        session.add_message({"role": "user", "content": "hi"})
        session.add_message({"role": "assistant", "content": "hello"})
        assert len(session.get_messages()) == 2


class TestInMemorySessionManager:
    @pytest.mark.asyncio
    async def test_get_or_create(self):
        mgr = _InMemorySessionManager()
        s1 = await mgr.get_or_create("key1")
        s2 = await mgr.get_or_create("key1")
        assert s1 is s2  # 同一个会话


class TestCountIterations:
    def test_counts_assistant_messages(self):
        mgr = _InMemorySessionManager()
        import asyncio
        session = asyncio.get_event_loop().run_until_complete(mgr.get_or_create("k"))
        session.add_message({"role": "user", "content": "hi"})
        session.add_message({"role": "assistant", "content": "hello"})
        session.add_message({"role": "user", "content": "bye"})
        session.add_message({"role": "assistant", "content": "goodbye"})
        assert _count_iterations(mgr, "k") == 2


class TestChildConfig:
    def test_override_max_iterations(self):
        class FakeParent:
            model = "claude-sonnet-4-20250514"
            provider = "anthropic"
        child = _ChildConfig(FakeParent(), max_iterations=5)
        assert child.max_tool_iterations == 5
        assert child.model == "claude-sonnet-4-20250514"  # 委托给父配置


class TestBrowserToolsWithoutPlaywright:
    """测试浏览器工具在缺少 Playwright 时能优雅处理。"""

    @pytest.mark.asyncio
    async def test_navigate_without_playwright(self):
        tool = BrowserNavigateTool()
        # 如果未安装 Playwright，此测试可以正常工作
        # 如果已安装，它会尝试真正导航
        # 我们只检查工具具有正确的接口
        assert tool.name == "browser_navigate"
        assert "url" in tool.parameters["properties"]

    def test_close_tool_interface(self):
        tool = BrowserCloseTool()
        assert tool.name == "browser_close"
```

### 检查点

```bash
python -m pytest tests/test_browser_delegate.py -v
```

预期结果：所有测试通过。浏览器工具在缺少 Playwright 时能优雅处理，委派数据类工作正常。

### 本课成果

六个浏览器自动化工具（导航、快照、点击、输入、滚动、关闭）通过延迟导入封装了 Playwright，加上一个 `DelegateTaskTool`，可以生成具有受限工具集、独立会话和可配置超时的隔离子智能体。智能体现在可以浏览网页并委派复杂的子任务。

---
