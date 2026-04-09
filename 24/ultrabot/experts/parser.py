"""将 agency-agents-zh markdown 人设文件解析为结构化的 ExpertPersona 对象。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ExpertPersona:
    """从 markdown 解析出的专家人设的结构化表示。

    每个人设对应一个 .md 文件。``raw_body``（去除 frontmatter 后的 markdown）
    既用作 LLM 系统提示词，其结构化字段又驱动搜索、路由和界面展示。
    """

    slug: str                       # 从文件名获取的 URL 安全标识
    name: str                       # 可读名称（例如 "前端开发者"）
    description: str = ""           # 来自 YAML frontmatter 的一行描述
    department: str = ""            # 从目录或 slug 前缀推断
    color: str = ""                 # 来自 frontmatter 的徽章/界面颜色
    identity: str = ""              # 人设的身份段落
    core_mission: str = ""          # 专家的职责
    key_rules: str = ""             # 约束和原则
    workflow: str = ""              # 逐步工作流程
    deliverables: str = ""          # 示例输出
    communication_style: str = ""   # 专家的沟通方式
    success_metrics: str = ""       # 有效性度量
    raw_body: str = ""              # 完整 markdown 正文（= 系统提示词）
    tags: list[str] = field(default_factory=list)  # 可搜索的关键词
    source_path: Path | None = None

    @property
    def system_prompt(self) -> str:
        """返回完整的 markdown 正文，可直接用作系统提示词。"""
        return self.raw_body

# 仍在 ultrabot/experts/parser.py 中

# 匹配文件顶部由 --- 分隔的 frontmatter 块。
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """提取 YAML frontmatter 并返回 ``(meta, body)``。

    使用简单的逐行解析器而非完整的 YAML 库，以保持依赖最小化。
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text             # 无 frontmatter — 整个文本即正文

    raw_yaml = m.group(1)
    body = text[m.end():]           # 闭合 --- 之后的所有内容

    meta: dict[str, str] = {}
    for line in raw_yaml.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        colon = line.find(":")
        if colon < 1:
            continue
        key = line[:colon].strip()
        val = line[colon + 1:].strip().strip('"').strip("'")
        meta[key] = val

    return meta, body

# 将中英文章节标题映射到 ExpertPersona 的字段名。
_SECTION_MAP: dict[str, str] = {
    # 中文标题（agency-agents-zh 语料库）
    "你的身份与记忆": "identity",
    "身份与记忆": "identity",
    "角色": "identity",
    "核心使命": "core_mission",
    "关键规则": "key_rules",
    "技术交付物": "deliverables",
    "交付物": "deliverables",
    "工作流程": "workflow",
    "沟通风格": "communication_style",
    "成功指标": "success_metrics",
    "学习与记忆": "identity",
    # 英文标题（上游）
    "your identity": "identity",
    "identity & memory": "identity",
    "core mission": "core_mission",
    "key rules": "key_rules",
    "technical deliverables": "deliverables",
    "deliverables": "deliverables",
    "workflow": "workflow",
    "communication style": "communication_style",
    "success metrics": "success_metrics",
    "learning & memory": "identity",
}


def _extract_sections(body: str) -> dict[str, str]:
    """按 ``## `` 标题拆分 markdown 正文并映射到字段名。"""
    sections: dict[str, list[str]] = {}
    current_field: str | None = None

    for line in body.splitlines():
        if line.startswith("## "):
            heading = line[3:].strip()
            normalised = heading.lower()
            field_name = _SECTION_MAP.get(normalised)
            if field_name is None:
                # 尝试子字符串匹配以处理部分标题。
                for key, fname in _SECTION_MAP.items():
                    if key in normalised:
                        field_name = fname
                        break
            current_field = field_name
            if current_field:
                sections.setdefault(current_field, [])
        elif current_field and current_field in sections:
            sections[current_field].append(line)

    return {k: "\n".join(v).strip() for k, v in sections.items()}

# 从标签中排除的常见中文停用词。
_STOP_WORDS = frozenset(
    "的 了 是 在 和 有 不 这 要 你 我 把 被 也 一 都 会 让 从 到 用 于 与 为 之".split()
)


def _extract_tags(persona: ExpertPersona) -> list[str]:
    """从人设中构建可搜索的关键词标签列表。"""
    tag_source = " ".join(
        filter(None, [persona.name, persona.description, persona.department])
    )
    tokens: set[str] = set()

    # 英文 / 字母数字词汇
    for word in re.findall(r"[A-Za-z0-9][\w\-]{1,}", tag_source):
        tokens.add(word.lower())

    # CJK 字符单字（减去停用词）+ 双字组
    cjk_chars = re.findall(r"[\u4e00-\u9fff]+", tag_source)
    for chunk in cjk_chars:
        for i in range(len(chunk)):
            ch = chunk[i]
            if ch not in _STOP_WORDS:
                tokens.add(ch)
        for i in range(len(chunk) - 1):
            tokens.add(chunk[i:i + 2])

    return sorted(tokens)


_DEPARTMENT_PREFIXES = {
    "engineering", "design", "marketing", "product", "finance",
    "game-development", "hr", "legal", "paid-media", "sales",
    "project-management", "testing", "support", "academic",
    "supply-chain", "spatial-computing", "specialized", "integrations",
}


def _infer_department(slug: str) -> str:
    """从 slug 前缀推断部门。"""
    for prefix in _DEPARTMENT_PREFIXES:
        tag = prefix.replace("-", "")
        slug_clean = slug.replace("-", "")
        if slug_clean.startswith(tag):
            return prefix
    return slug.split("-")[0] if "-" in slug else ""

def parse_persona_file(path: Path) -> ExpertPersona:
    """将单个 agency-agents-zh markdown 文件解析为 ExpertPersona。"""
    text = path.read_text(encoding="utf-8")
    slug = path.stem  # 例如 "engineering-frontend-developer"

    meta, body = _parse_frontmatter(text)
    sections = _extract_sections(body)

    # 从父目录名或 slug 推断部门。
    department = path.parent.name if path.parent.name in _DEPARTMENT_PREFIXES else ""
    if not department:
        department = _infer_department(slug)

    persona = ExpertPersona(
        slug=slug,
        name=meta.get("name", slug),
        description=meta.get("description", ""),
        department=department,
        color=meta.get("color", ""),
        identity=sections.get("identity", ""),
        core_mission=sections.get("core_mission", ""),
        key_rules=sections.get("key_rules", ""),
        workflow=sections.get("workflow", ""),
        deliverables=sections.get("deliverables", ""),
        communication_style=sections.get("communication_style", ""),
        success_metrics=sections.get("success_metrics", ""),
        raw_body=body.strip(),
        source_path=path,
    )
    persona.tags = _extract_tags(persona)
    return persona


def parse_persona_text(text: str, slug: str = "custom") -> ExpertPersona:
    """将原始 markdown 文本解析为 ExpertPersona，无需文件。

    适用于测试或动态创建的人设。
    """
    meta, body = _parse_frontmatter(text)
    sections = _extract_sections(body)
    department = _infer_department(slug)

    persona = ExpertPersona(
        slug=slug,
        name=meta.get("name", slug),
        description=meta.get("description", ""),
        department=department,
        color=meta.get("color", ""),
        identity=sections.get("identity", ""),
        core_mission=sections.get("core_mission", ""),
        key_rules=sections.get("key_rules", ""),
        workflow=sections.get("workflow", ""),
        deliverables=sections.get("deliverables", ""),
        communication_style=sections.get("communication_style", ""),
        success_metrics=sections.get("success_metrics", ""),
        raw_body=body.strip(),
    )
    persona.tags = _extract_tags(persona)
    return persona
