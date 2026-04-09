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
