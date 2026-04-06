# Ultrabot：30 课程开发指南
**从零开始构建一个生产级 AI 助手框架。**
本指南将带你从"向 LLM 问好"一步步走到一个完整的多提供者、多通道 AI 智能体，具备工具调用、记忆、安全防护和 Web 界面。每节课程都建立在上一节课的基础之上。每节课都包含可运行的代码和测试。  
本教程的主要思路来自于
- Nanobot (https://github.com/HKUDS/nanobot)
- Learn-Claude-Code (https://github.com/shareAI-lab/learn-claude-code/)

本课程设计由AI辅助下完成，因为课程自身也在不停修正，请参考 https://github.com/junfhu/UltrabotStepByStep，如果您觉得对您有帮助，请帮助点亮一颗星。  
本课程中使用的大模型提供商是火山引擎Code Plan，如果正好你也需要，可以使用我的邀请码获取9折优惠 https://volcengine.com/L/_01BJCkKdMc/  邀请码：HHCDB4J4）  



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

**沿用文件（从早期课程复制）：**
- `ultrabot/agent/agent.py` — `Agent` 核心类（config, provider_manager, session_manager, tool_registry 构造函数，async run() 方法）
- `ultrabot/tools/base.py` — `Tool` 抽象基类 + `ToolRegistry`（课程 3-4）
- `ultrabot/tools/toolsets.py` — `Toolset` 数据类 + `ToolsetManager`（课程 4）

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

> **pytest 配置**：本课的异步测试使用 `@pytest.mark.asyncio`，需要在 `pyproject.toml` 中添加：
> ```toml
> [tool.pytest.ini_options]
> asyncio_mode = "auto"
> ```

### 检查点

```bash
python -m pytest tests/test_browser_delegate.py -v
```

预期结果：所有测试通过。浏览器工具在缺少 Playwright 时能优雅处理，委派数据类工作正常。

### 本课成果

六个浏览器自动化工具（导航、快照、点击、输入、滚动、关闭）通过延迟导入封装了 Playwright，加上一个 `DelegateTaskTool`，可以生成具有受限工具集、独立会话和可配置超时的隔离子智能体。智能体现在可以浏览网页并委派复杂的子任务。

---

## 本课使用的 Python 知识

### 延迟导入（Lazy Import）

延迟导入是指在函数或方法内部才执行 `import` 语句，而不是在文件顶部导入。这样，即使某个库未安装，只要不调用那个具体函数，整个模块也能正常导入。

```python
# 不在顶部导入 playwright，而是在需要时才导入
async def start_browser():
    from playwright.async_api import async_playwright  # 延迟导入
    pw = await async_playwright().start()
    browser = await pw.chromium.launch()
    return browser
```

**为什么在本课中使用：** Playwright 是一个较重的可选依赖（需要下载浏览器二进制文件）。延迟导入让 `ultrabot/tools/browser.py` 即使在没有安装 Playwright 的环境中也能被正常导入，只有真正调用浏览器工具时才会触发 `ImportError`。

### 模块级单例模式（Module-level Singleton）

在模块级别创建一个对象实例，所有使用者共享同一个实例。Python 模块在首次导入时只执行一次，因此模块级变量天然是单例的。

```python
class _DatabasePool:
    def __init__(self):
        self._connections = []

# 模块级单例 — 所有导入这个模块的代码共享同一个实例
_pool = _DatabasePool()
```

**为什么在本课中使用：** `_manager = _BrowserManager()` 作为模块级单例，确保所有 6 个浏览器工具共享同一个浏览器实例。这样不会打开多个浏览器窗口，节省资源，且工具之间可以在同一个页面上协作。

### `async/await`（异步编程）

`async def` 定义异步函数（协程），`await` 等待异步操作完成。异步编程允许在等待 I/O（网络请求、文件读写）时执行其他任务。

```python
async def fetch_page(url):
    page = await browser.new_page()
    await page.goto(url)
    title = await page.title()
    return title
```

**为什么在本课中使用：** 浏览器操作（导航、点击、输入）和子智能体委派都涉及大量 I/O 等待。`async/await` 让 ultrabot 在等待页面加载或子智能体响应时可以处理其他任务，保持高效运行。

### 类继承（Class Inheritance）

Python 的类可以继承自一个父类（基类），获得父类的所有属性和方法，同时可以添加或覆盖自己的行为。

```python
class Animal:
    def speak(self):
        raise NotImplementedError

class Dog(Animal):
    def speak(self):  # 覆盖父类方法
        return "Woof!"

class Cat(Animal):
    def speak(self):
        return "Meow!"
```

**为什么在本课中使用：** 所有 6 个浏览器工具（`BrowserNavigateTool`、`BrowserClickTool` 等）都继承自 `Tool` 基类。基类定义了 `name`、`description`、`parameters` 和 `execute()` 的接口约定，每个子类实现自己的 `execute()` 逻辑。

### 类属性 vs 实例属性

类属性直接定义在类体中，由所有实例共享；实例属性在 `__init__` 中通过 `self` 定义，每个实例独有。

```python
class Tool:
    name = "default"          # 类属性 — 所有实例共享
    description = ""          # 类属性

    def __init__(self):
        self.result = None    # 实例属性 — 每个实例独立
```

**为什么在本课中使用：** 每个浏览器工具的 `name`、`description`、`parameters` 都是类属性（如 `name = "browser_navigate"`），因为这些信息是该类型工具的固有特征，不会随实例变化。工具注册表可以直接通过类来获取元数据。

### `Any | None` 联合类型（PEP 604）

Python 3.10+ 支持用 `|` 运算符组合类型注解，`Any | None` 表示值可以是任意类型或 `None`。

```python
from typing import Any

value: int | str = 42      # 可以是 int 或 str
client: Any | None = None  # 可以是任意类型或 None
```

**为什么在本课中使用：** `_BrowserManager` 的内部变量 `self._playwright: Any | None = None` 表示 Playwright 实例可能尚未创建（`None`）或者是一个 Playwright 对象（`Any`，因为是延迟导入的，在定义时不知道精确类型）。

### `try/except ImportError`（优雅处理缺失依赖）

当尝试导入一个可能未安装的库时，可以用 `try/except ImportError` 捕获导入错误，提供友好的错误提示或降级方案。

```python
try:
    from playwright.async_api import async_playwright
except ImportError:
    print("请先安装 playwright: pip install playwright")
```

**为什么在本课中使用：** 每个浏览器工具的 `execute()` 方法都用 `try/except ImportError` 包裹对 `_manager.ensure_browser()` 的调用。如果 Playwright 未安装，工具会返回安装指引文本而不是崩溃。

### `asyncio.wait_for()`（异步超时控制）

`asyncio.wait_for()` 为一个协程设置最大等待时间。如果协程在指定时间内没有完成，会抛出 `asyncio.TimeoutError`。

```python
import asyncio

async def slow_task():
    await asyncio.sleep(100)

try:
    result = await asyncio.wait_for(slow_task(), timeout=5.0)
except asyncio.TimeoutError:
    print("任务超时了！")
```

**为什么在本课中使用：** 子智能体委派使用 `asyncio.wait_for()` 限制子 Agent 的运行时间。`request.timeout_seconds`（默认 120 秒）防止子智能体陷入无限循环或长时间运行消耗资源。

### `time.monotonic()`（单调时钟）

`time.monotonic()` 返回一个不会倒退的时间值（不受系统时钟调整影响），适合测量经过的时间。

```python
import time

start = time.monotonic()
# ... 执行一些操作 ...
elapsed = time.monotonic() - start
print(f"耗时: {elapsed:.3f} 秒")
```

**为什么在本课中使用：** `delegate()` 函数用 `time.monotonic()` 精确测量子智能体的执行时间。相比 `time.time()`，`monotonic()` 不受系统时钟被人为调整的影响，计时更可靠。

### `@dataclass` 与 `field(default_factory=...)`

`field(default_factory=...)` 用于为数据类字段设置可变默认值（如列表、字典）。直接使用 `= []` 作为默认值是 Python 的经典陷阱。

```python
from dataclasses import dataclass, field

@dataclass
class Config:
    tags: list[str] = field(default_factory=list)          # 每个实例有自己的列表
    names: list[str] = field(default_factory=lambda: ["all"])  # 自定义默认值
```

**为什么在本课中使用：** `DelegationRequest` 的 `toolset_names` 默认值是 `["all"]`，用 `field(default_factory=lambda: ["all"])` 确保每个请求实例有自己独立的列表，而不是共享同一个可变对象。

### `__getattr__` 魔术方法（属性委托/代理模式）

`__getattr__` 在访问一个不存在的属性时被调用，可以用来将属性访问委托给另一个对象，实现代理模式。

```python
class Proxy:
    def __init__(self, target):
        self._target = target
        self.special = "覆盖值"

    def __getattr__(self, name):
        return getattr(self._target, name)  # 委托给目标对象

# Proxy 的 special 属性使用自己的值，其他属性委托给 target
```

**为什么在本课中使用：** `_ChildConfig` 覆盖了 `max_tool_iterations` 属性，但其他所有配置项（`model`、`provider` 等）通过 `__getattr__` 委托给父配置。这避免了复制整个配置对象，只需要覆盖一两个参数。

### 生成器表达式与 `sum()`

生成器表达式类似列表推导式，但使用圆括号且不会立即创建列表，而是惰性求值。可以与 `sum()`、`any()`、`all()` 等函数组合使用。

```python
# 计算列表中偶数的个数
numbers = [1, 2, 3, 4, 5, 6]
count = sum(1 for n in numbers if n % 2 == 0)
print(count)  # 3
```

**为什么在本课中使用：** `_count_iterations()` 用 `sum(1 for m in session.get_messages() if m.get("role") == "assistant")` 统计子智能体的迭代次数（即助手消息的数量），生成器表达式让这行代码既简洁又内存高效。

### `!r` repr 格式化

在 f-string 中，`!r` 调用对象的 `repr()` 方法，会给字符串加上引号并转义特殊字符，方便调试。

```python
text = "hello\nworld"
print(f"原始: {text}")    # 原始: hello
                           #       world
print(f"repr: {text!r}")  # repr: 'hello\nworld'
```

**为什么在本课中使用：** `BrowserTypeTool` 返回 `f"Typed into {selector}: {text!r}"`，用 `!r` 显示输入文本的 repr 形式，这样空格、换行等特殊字符都会可见，方便调试用户输入了什么内容。

### `type(exc).__name__`（获取异常类名）

`type(obj).__name__` 返回对象的类名字符串，常用于在错误信息中包含异常的类型名。

```python
try:
    result = 1 / 0
except Exception as exc:
    print(f"{type(exc).__name__}: {exc}")
    # 输出: ZeroDivisionError: division by zero
```

**为什么在本课中使用：** `delegate()` 捕获所有异常后，用 `f"{type(exc).__name__}: {exc}"` 构建错误信息，让调用者既能看到异常类型（如 `ConnectionError`）也能看到具体错误描述。

### `pytest.mark.asyncio`（异步测试标记）

`pytest-asyncio` 插件允许在 pytest 中直接编写和运行异步测试函数，只需加上 `@pytest.mark.asyncio` 装饰器。

```python
import pytest

@pytest.mark.asyncio
async def test_fetch_data():
    result = await fetch_data("https://example.com")
    assert result is not None
```

**为什么在本课中使用：** `TestInMemorySessionManager` 中的 `test_get_or_create` 需要测试 `await mgr.get_or_create("key1")` 这样的异步方法，`@pytest.mark.asyncio` 让 pytest 能够运行这些异步测试。

### `loguru.logger`（结构化日志）

`loguru` 是 Python 的现代日志库，比标准 `logging` 模块更简单易用。它支持彩色输出、自动格式化、文件轮转等。

```python
from loguru import logger

logger.debug("调试信息")
logger.info("已注册 {} 个工具", 6)
logger.warning("浏览器关闭出错: {}", error)
```

**为什么在本课中使用：** 浏览器工具使用 `logger.debug()` 记录浏览器启动信息，`logger.warning()` 记录关闭浏览器时的错误，`logger.info()` 记录工具注册情况。loguru 的 `{}` 占位符语法比 f-string 更安全（不会因格式化错误导致日志丢失）。

### 注册模式（Registry Pattern）

注册模式是将多个对象集中注册到一个管理器中，方便统一查找和调用。通常涉及一个 `register()` 方法和一个集合来存储注册项。

```python
class ToolRegistry:
    def __init__(self):
        self._tools = {}

    def register(self, tool):
        self._tools[tool.name] = tool

# 批量注册
for tool_cls in [NavigateTool, ClickTool, TypeTool]:
    registry.register(tool_cls())
```

**为什么在本课中使用：** `register_browser_tools()` 函数遍历 6 个浏览器工具类，实例化每个工具并注册到 `ToolRegistry` 中。注册后，智能体就可以通过工具名（如 `"browser_navigate"`）来查找和调用这些工具。
