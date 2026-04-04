"""专家路由器 -- 为每条入站消息选择合适的专家。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from ultrabot.experts.parser import ExpertPersona
    from ultrabot.experts.registry import ExpertRegistry


@dataclass(slots=True)
class RouteResult:
    """将用户消息路由到专家的结果。

    属性：
        persona: 选中的 ExpertPersona，或 None 表示使用默认代理。
        cleaned_message: 去除路由命令后的用户消息。
        source: 选择方式："command"、"sticky"、"auto" 或 "default"。
    """
    persona: ExpertPersona | None
    cleaned_message: str
    source: str = "default"

# @slug ...  或  /expert slug ...
_AT_PATTERN = re.compile(r"^@([\w-]+)\s*", re.UNICODE)
_SLASH_PATTERN = re.compile(
    r"^/expert\s+([\w-]+)\s*", re.UNICODE | re.IGNORECASE
)
# /expert off  或  @default
_OFF_PATTERNS = re.compile(
    r"^(?:/expert\s+off|@default)\b\s*", re.UNICODE | re.IGNORECASE
)
# /experts（列出全部）或  /experts query（搜索）
_LIST_PATTERN = re.compile(
    r"^/experts(?:\s+(.+))?\s*$", re.UNICODE | re.IGNORECASE
)

class ExpertRouter:
    """将入站消息路由到专家人设。

    参数：
        registry: 包含已加载人设的 ExpertRegistry。
        auto_route: 是否使用基于 LLM 的自动路由。
        provider_manager: 可选的 ProviderManager，用于自动路由。
    """

    def __init__(
        self,
        registry: "ExpertRegistry",
        auto_route: bool = False,
        provider_manager: Any | None = None,
    ) -> None:
        self._registry = registry
        self._auto_route = auto_route
        self._provider = provider_manager
        # 会话-slug 粘性映射：session_key -> 专家 slug
        self._sticky: dict[str, str] = {}

    async def route(
        self,
        message: str,
        session_key: str,
    ) -> RouteResult:
        """确定哪个专家应处理 *message*。"""
        # 1. 停用命令
        m = _OFF_PATTERNS.match(message)
        if m:
            self._sticky.pop(session_key, None)
            cleaned = message[m.end():].strip() or "OK, switched back to default mode."
            return RouteResult(persona=None, cleaned_message=cleaned, source="command")

        # 2. 列表命令
        m = _LIST_PATTERN.match(message)
        if m:
            query = (m.group(1) or "").strip()
            listing = self._build_listing(query)
            return RouteResult(persona=None, cleaned_message=listing, source="command")

        # 3. 显式专家命令
        slug, cleaned = self._extract_command(message)
        if slug:
            persona = self._resolve_slug(slug)
            if persona:
                self._sticky[session_key] = persona.slug
                logger.info("Routed session {!r} to expert {!r} (command)",
                            session_key, persona.slug)
                return RouteResult(persona=persona, cleaned_message=cleaned,
                                   source="command")
            logger.warning("Unknown expert slug: {!r}", slug)

        # 4. 粘性会话
        sticky_slug = self._sticky.get(session_key)
        if sticky_slug:
            persona = self._registry.get(sticky_slug)
            if persona:
                return RouteResult(persona=persona, cleaned_message=message,
                                   source="sticky")
            del self._sticky[sticky_slug]  # 已过期 — 清理

        # 5. 自动路由（基于 LLM）
        if self._auto_route and self._provider and len(self._registry) > 0:
            persona = await self._auto_select(message)
            if persona:
                self._sticky[session_key] = persona.slug
                logger.info("Auto-routed session {!r} to expert {!r}",
                            session_key, persona.slug)
                return RouteResult(persona=persona, cleaned_message=message,
                                   source="auto")

        # 6. 默认
        return RouteResult(persona=None, cleaned_message=message, source="default")

    def clear_sticky(self, session_key: str) -> None:
        self._sticky.pop(session_key, None)

    def get_sticky(self, session_key: str) -> str | None:
        return self._sticky.get(session_key)

    # -- 内部方法（仍在 ExpertRouter 内）--

    def _extract_command(self, message: str) -> tuple[str | None, str]:
        """尝试从消息中提取显式专家命令。"""
        m = _AT_PATTERN.match(message)
        if m:
            return m.group(1), message[m.end():].strip() or message

        m = _SLASH_PATTERN.match(message)
        if m:
            return m.group(1), message[m.end():].strip() or message

        return None, message

    def _resolve_slug(self, slug: str) -> "ExpertPersona | None":
        """在注册表中查找 slug，先精确匹配再按名称匹配。"""
        persona = self._registry.get(slug)
        if persona:
            return persona
        return self._registry.get_by_name(slug)

    def _build_listing(self, query: str) -> str:
        """构建格式化的专家列表，可选过滤。"""
        if query:
            results = self._registry.search(query, limit=20)
            if not results:
                return f"No experts found for '{query}'."
            lines = [f"**Experts matching '{query}':**\n"]
            for p in results:
                lines.append(f"- `@{p.slug}` -- {p.name}: {p.description[:60]}")
            return "\n".join(lines)

        departments = self._registry.departments()
        if not departments:
            return "No experts loaded. Run `ultrabot experts sync` to download."

        lines = [f"**{len(self._registry)} experts across {len(departments)} departments:**\n"]
        for dept in departments:
            experts = self._registry.list_department(dept)
            names = ", ".join(f"`{p.slug}`" for p in experts[:5])
            suffix = f" ... +{len(experts) - 5} more" if len(experts) > 5 else ""
            lines.append(f"- **{dept}** ({len(experts)}): {names}{suffix}")
        lines.append("\nUse `@slug` to activate an expert, `/experts query` to search.")
        return "\n".join(lines)

    async def _auto_select(self, message: str) -> "ExpertPersona | None":
        """使用 LLM 调用为消息选择最佳专家。"""
        catalog = self._registry.build_catalog()

        system = (
            "You are an expert routing assistant. Given the user's message, "
            "pick the single best expert from the catalog below. "
            "Return ONLY the expert slug (e.g. 'engineering-frontend-developer') "
            "or 'none' if no expert is a good match.\n\n"
            f"EXPERT CATALOG:\n{catalog}"
        )

        try:
            response = await self._provider.chat_with_failover(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": message},
                ],
                max_tokens=60,
                temperature=0.0,
            )
            slug = (response.content or "").strip().lower().strip("`'\"")
            if slug and slug != "none":
                return self._registry.get(slug)
        except Exception:
            logger.exception("Auto-route LLM call failed")

        return None
