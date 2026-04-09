# ultrabot/experts/registry.py
"""专家注册表 -- 加载、索引和搜索专家人设。"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Iterable, Sequence

from loguru import logger

from ultrabot.experts.parser import ExpertPersona, parse_persona_file


class ExpertRegistry:
    """ExpertPersona 对象的内存注册表。

    从 ``.md`` 文件目录（每个专家一个文件）加载人设。
    注册表支持按 slug、部门查找和自由文本搜索。
    """

    def __init__(self, experts_dir: Path | None = None) -> None:
        self._experts: dict[str, ExpertPersona] = {}
        self._by_department: dict[str, list[str]] = defaultdict(list)
        self._experts_dir = experts_dir

    # -- 加载 ----------------------------------------------------------

    def load_directory(self, directory: Path | None = None) -> int:
        """扫描 *directory* 中的 ``.md`` 人设文件并加载。

        支持平铺和嵌套（部门子目录）两种布局。
        返回加载的人设数量。
        """
        directory = directory or self._experts_dir
        if directory is None:
            raise ValueError("No experts directory specified.")
        directory = Path(directory)
        if not directory.is_dir():
            logger.warning("Experts directory does not exist: {}", directory)
            return 0

        count = 0
        for md_file in sorted(directory.rglob("*.md")):
            if md_file.name.startswith("_") or md_file.name.upper() == "README.MD":
                continue
            try:
                persona = parse_persona_file(md_file)
                self.register(persona)
                count += 1
            except Exception:
                logger.exception("Failed to parse persona from {}", md_file)

        logger.info("Loaded {} expert persona(s) from {}", count, directory)
        return count

    def register(self, persona: ExpertPersona) -> None:
        """向注册表中添加或替换一个人设。"""
        if persona.slug in self._experts:
            old = self._experts[persona.slug]
            if old.department and old.slug in self._by_department.get(old.department, []):
                self._by_department[old.department].remove(old.slug)

        self._experts[persona.slug] = persona
        if persona.department:
            self._by_department[persona.department].append(persona.slug)

    def unregister(self, slug: str) -> None:
        """按 slug 移除一个人设。如未找到则无操作。"""
        persona = self._experts.pop(slug, None)
        if persona and persona.department:
            dept_list = self._by_department.get(persona.department, [])
            if slug in dept_list:
                dept_list.remove(slug)

    # -- 查找 -----------------------------------------------------------

    def get(self, slug: str) -> ExpertPersona | None:
        return self._experts.get(slug)

    def get_by_name(self, name: str) -> ExpertPersona | None:
        """按可读名称查找人设（不区分大小写）。"""
        name_lower = name.lower()
        for persona in self._experts.values():
            if persona.name.lower() == name_lower:
                return persona
        return None

    def list_all(self) -> list[ExpertPersona]:
        """返回所有人设，按部门然后 slug 排序。"""
        return sorted(self._experts.values(), key=lambda p: (p.department, p.slug))

    def list_department(self, department: str) -> list[ExpertPersona]:
        slugs = self._by_department.get(department, [])
        return [self._experts[s] for s in sorted(slugs) if s in self._experts]

    def departments(self) -> list[str]:
        return sorted(d for d, slugs in self._by_department.items() if slugs)

    # -- 搜索 -----------------------------------------------------------

    def search(self, query: str, limit: int = 10) -> list[ExpertPersona]:
        """对名称、描述、标签和部门进行全文搜索。

        返回最多 *limit* 条结果，按相关性分数降序排列。
        """
        query_lower = query.lower()
        query_tokens = set(query_lower.split())

        scored: list[tuple[float, ExpertPersona]] = []
        for persona in self._experts.values():
            score = self._score_match(persona, query_lower, query_tokens)
            if score > 0:
                scored.append((score, persona))

        scored.sort(key=lambda x: -x[0])
        return [p for _, p in scored[:limit]]

    @staticmethod
    def _score_match(
        persona: ExpertPersona,
        query_lower: str,
        query_tokens: set[str],
    ) -> float:
        """计算人设与查询的相关性分数。"""
        score = 0.0
        if query_lower == persona.slug:
            score += 100.0
        if query_lower == persona.name.lower():
            score += 100.0
        if query_lower in persona.slug:
            score += 30.0
        if query_lower in persona.name.lower():
            score += 30.0
        if query_lower in persona.description.lower():
            score += 15.0
        if query_lower == persona.department:
            score += 20.0

        tag_set = set(persona.tags)
        for token in query_tokens:
            if token in tag_set:
                score += 5.0
        for tag in persona.tags:
            for token in query_tokens:
                if token in tag or tag in token:
                    score += 2.0

        return score

    # -- 目录（用于 LLM 路由）----------------------------------------

    def build_catalog(
        self,
        personas: Sequence[ExpertPersona] | None = None,
    ) -> str:
        """构建简洁的专家目录字符串，供 LLM 路由使用。"""
        items = personas or self.list_all()
        if not items:
            return "(no experts loaded)"

        by_dept: dict[str, list[ExpertPersona]] = defaultdict(list)
        for p in items:
            by_dept[p.department or "other"].append(p)

        lines: list[str] = []
        for dept in sorted(by_dept):
            lines.append(f"## {dept}")
            for p in sorted(by_dept[dept], key=lambda x: x.slug):
                desc = p.description[:80] if p.description else p.name
                lines.append(f"- {p.slug}: {p.name} -- {desc}")
            lines.append("")
        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self._experts)

    def __contains__(self, slug: str) -> bool:
        return slug in self._experts

    def __repr__(self) -> str:
        return f"<ExpertRegistry experts={len(self._experts)}>"
