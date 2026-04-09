# tests/test_experts_router.py
"""专家路由器和同步模块的测试。"""

import pytest

from ultrabot.experts.parser import parse_persona_text
from ultrabot.experts.registry import ExpertRegistry
from ultrabot.experts.router import ExpertRouter, RouteResult


CODER_MD = """\
---
name: "Coder"
description: "Expert Python programmer"
---
## Your identity
You write Python code.
"""

WRITER_MD = """\
---
name: "Writer"
description: "Creative content writer"
---
## Your identity
You write compelling content.
"""


@pytest.fixture
def registry():
    reg = ExpertRegistry()
    reg.register(parse_persona_text(CODER_MD, slug="coder"))
    reg.register(parse_persona_text(WRITER_MD, slug="writer"))
    return reg


@pytest.fixture
def router(registry):
    return ExpertRouter(registry, auto_route=False)


class TestCommandRouting:
    @pytest.mark.asyncio
    async def test_at_command(self, router):
        result = await router.route("@coder Fix this bug", session_key="s1")
        assert result.source == "command"
        assert result.persona is not None
        assert result.persona.slug == "coder"
        assert result.cleaned_message == "Fix this bug"

    @pytest.mark.asyncio
    async def test_slash_command(self, router):
        result = await router.route("/expert writer Draft an email", session_key="s1")
        assert result.persona.slug == "writer"
        assert result.cleaned_message == "Draft an email"

    @pytest.mark.asyncio
    async def test_expert_off(self, router):
        # 先激活一个专家
        await router.route("@coder hello", session_key="s1")
        assert router.get_sticky("s1") == "coder"

        # 然后停用
        result = await router.route("/expert off", session_key="s1")
        assert result.persona is None
        assert result.source == "command"
        assert router.get_sticky("s1") is None

    @pytest.mark.asyncio
    async def test_unknown_slug_falls_through(self, router):
        result = await router.route("@nonexistent hello", session_key="s1")
        assert result.source == "default"
        assert result.persona is None


class TestStickySession:
    @pytest.mark.asyncio
    async def test_sticky_persists(self, router):
        await router.route("@coder hello", session_key="s1")
        # 不带命令的下一条消息应继续使用 coder
        result = await router.route("What about this?", session_key="s1")
        assert result.source == "sticky"
        assert result.persona.slug == "coder"

    @pytest.mark.asyncio
    async def test_different_sessions_independent(self, router):
        await router.route("@coder hello", session_key="s1")
        result = await router.route("Hello", session_key="s2")
        assert result.source == "default"  # s2 没有粘性会话


class TestListCommand:
    @pytest.mark.asyncio
    async def test_list_all(self, router):
        result = await router.route("/experts", session_key="s1")
        assert result.source == "command"
        assert "2 experts" in result.cleaned_message

    @pytest.mark.asyncio
    async def test_list_search(self, router):
        result = await router.route("/experts Python", session_key="s1")
        assert "coder" in result.cleaned_message.lower()
