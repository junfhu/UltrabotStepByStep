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
