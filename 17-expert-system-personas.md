# 课程 17：专家系统 — 人设

**目标：** 构建一个基于人设的专家系统，将 markdown 人设文件解析为结构化的 dataclass，并提供可搜索的注册表。

**你将学到：**
- 包含所有结构化字段的 `ExpertPersona` dataclass
- 无需外部 YAML 库的 YAML frontmatter + markdown 章节解析
- 支持部门索引和相关性评分搜索的 `ExpertRegistry`
- 从中日韩（CJK）+ 英文文本中提取标签
- 从目录树中加载人设

**新建文件：**
- `ultrabot/experts/__init__.py` — 包导出和内置人设路径
- `ultrabot/experts/parser.py` — markdown 人设解析器，含 frontmatter 提取
- `ultrabot/experts/registry.py` — 内存注册表，支持搜索和目录生成

### 步骤 1：ExpertPersona Dataclass

每个专家人设都是一个丰富的结构化对象，从 markdown 文件中解析而来。这些
markdown 文件来自 [agency-agents-zh](https://github.com/jnMetaCode/agency-agents-zh)
仓库 — 包含 187 个领域专家，从前端开发者到法律顾问。

```python
# ultrabot/experts/parser.py
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
```

关键设计决策：
- **`slots=True`** 在加载数百个人设时保持低内存占用。
- **`raw_body` 作为系统提示词** — 整个 markdown 正文就是 LLM 指令。
- **`tags`** 在初始化后计算，用于搜索索引。

### 步骤 2：YAML Frontmatter 解析器（无需 PyYAML）

我们使用简单的正则表达式 + 逐行扫描来解析 frontmatter — 无需外部 YAML
库。这将依赖占用保持在最低限度。

```python
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
```

### 步骤 3：Markdown 章节提取

人设文件使用 `## ` 标题来分隔章节。我们将中英文标题名映射到 dataclass 字段名。

```python
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
```

### 步骤 4：标签提取和部门推断

标签结合了英文词汇和 CJK 双字组（bigram），实现高效的多语言搜索。

```python
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
```

### 步骤 5：公开解析 API

两个入口：基于文件的用于生产环境，基于文本的用于测试。

```python
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
```

### 步骤 6：ExpertRegistry

注册表负责加载、索引和搜索人设。支持按 slug、名称、部门查找以及自由文本相关性搜索。

```python
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
```

### 步骤 7：包初始化

```python
# ultrabot/experts/__init__.py
"""专家系统 -- 具备真实代理能力的领域专家人设。"""

from pathlib import Path

from ultrabot.experts.parser import ExpertPersona, parse_persona_file, parse_persona_text
from ultrabot.experts.registry import ExpertRegistry
from ultrabot.experts.router import ExpertRouter, RouteResult

#: 随包分发的内置人设 markdown 文件路径。
BUNDLED_PERSONAS_DIR: Path = Path(__file__).parent / "personas"

__all__ = [
    "BUNDLED_PERSONAS_DIR",
    "ExpertPersona",
    "ExpertRegistry",
    "ExpertRouter",
    "RouteResult",
    "parse_persona_file",
    "parse_persona_text",
]
```

### 测试

```python
# tests/test_experts_persona.py
"""专家人设解析器和注册表的测试。"""

import tempfile
from pathlib import Path

import pytest

from ultrabot.experts.parser import (
    ExpertPersona,
    parse_persona_file,
    parse_persona_text,
    _parse_frontmatter,
    _extract_sections,
    _extract_tags,
)
from ultrabot.experts.registry import ExpertRegistry


# -- 用于测试的示例 markdown 人设 --

SAMPLE_PERSONA_MD = """\
---
name: "前端开发者"
description: "React/Vue 前端工程专家"
color: "#61dafb"
---

# 前端开发者

## 你的身份与记忆

你是一位资深的前端开发工程师。

## 核心使命

构建高质量的用户界面。

## 关键规则

- 使用TypeScript
- 编写单元测试
- 遵循无障碍标准

## 工作流程

1. 需求分析
2. 组件设计
3. 编码实现
4. 测试验证
"""


class TestFrontmatterParsing:
    def test_basic_frontmatter(self):
        meta, body = _parse_frontmatter(SAMPLE_PERSONA_MD)
        assert meta["name"] == "前端开发者"
        assert meta["description"] == "React/Vue 前端工程专家"
        assert meta["color"] == "#61dafb"
        assert "# 前端开发者" in body

    def test_no_frontmatter(self):
        meta, body = _parse_frontmatter("Just plain text")
        assert meta == {}
        assert body == "Just plain text"


class TestSectionExtraction:
    def test_chinese_sections(self):
        _, body = _parse_frontmatter(SAMPLE_PERSONA_MD)
        sections = _extract_sections(body)
        assert "identity" in sections
        assert "资深" in sections["identity"]
        assert "core_mission" in sections
        assert "key_rules" in sections
        assert "workflow" in sections


class TestParsePersona:
    def test_parse_text(self):
        persona = parse_persona_text(SAMPLE_PERSONA_MD, slug="engineering-frontend")
        assert persona.slug == "engineering-frontend"
        assert persona.name == "前端开发者"
        assert persona.description == "React/Vue 前端工程专家"
        assert "资深" in persona.identity
        assert "高质量" in persona.core_mission
        assert persona.system_prompt  # raw_body 非空

    def test_parse_file(self, tmp_path):
        md_file = tmp_path / "engineering-frontend-developer.md"
        md_file.write_text(SAMPLE_PERSONA_MD, encoding="utf-8")
        persona = parse_persona_file(md_file)
        assert persona.slug == "engineering-frontend-developer"
        assert persona.source_path == md_file

    def test_tags_extracted(self):
        persona = parse_persona_text(SAMPLE_PERSONA_MD, slug="engineering-frontend")
        assert len(persona.tags) > 0
        # 应包含中文名称的双字组
        assert "前端" in persona.tags


class TestExpertRegistry:
    def test_register_and_lookup(self):
        registry = ExpertRegistry()
        persona = parse_persona_text(SAMPLE_PERSONA_MD, slug="test-dev")
        registry.register(persona)

        assert len(registry) == 1
        assert "test-dev" in registry
        assert registry.get("test-dev") is persona

    def test_search(self):
        registry = ExpertRegistry()
        registry.register(parse_persona_text(SAMPLE_PERSONA_MD, slug="eng-frontend"))
        results = registry.search("前端")
        assert len(results) >= 1
        assert results[0].slug == "eng-frontend"

    def test_load_directory(self, tmp_path):
        # 写入两个人设文件
        for name in ("dev-a", "dev-b"):
            (tmp_path / f"{name}.md").write_text(
                f"---\nname: {name}\n---\n## Your identity\nI am {name}.",
                encoding="utf-8",
            )
        # README 应被跳过
        (tmp_path / "README.md").write_text("# Readme")

        registry = ExpertRegistry(experts_dir=tmp_path)
        count = registry.load_directory()
        assert count == 2
        assert "dev-a" in registry
        assert "dev-b" in registry

    def test_build_catalog(self):
        registry = ExpertRegistry()
        persona = parse_persona_text(SAMPLE_PERSONA_MD, slug="eng-fe")
        registry.register(persona)
        catalog = registry.build_catalog()
        assert "eng-fe" in catalog
        assert "前端开发者" in catalog

    def test_unregister(self):
        registry = ExpertRegistry()
        persona = parse_persona_text(SAMPLE_PERSONA_MD, slug="rm-me")
        registry.register(persona)
        assert len(registry) == 1
        registry.unregister("rm-me")
        assert len(registry) == 0
```

### 检查点

```bash
# 创建一个自定义专家 YAML，加载它并验证
mkdir -p /tmp/test_experts

cat > /tmp/test_experts/my-coder.md << 'EOF'
---
name: "My Coder"
description: "A custom coding assistant"
---

## Your identity

You are an expert Python programmer.

## Core mission

Write clean, tested Python code.
EOF

python -c "
from ultrabot.experts import ExpertRegistry
reg = ExpertRegistry()
count = reg.load_directory('/tmp/test_experts')
print(f'Loaded {count} expert(s)')
for e in reg.list_all():
    print(f'  - {e.slug}: {e.name} ({e.department})')
    print(f'    Tags: {e.tags[:5]}')
print(f'Search \"coder\": {[e.slug for e in reg.search(\"coder\")]}')
"
```

预期输出：
```
Loaded 1 expert(s)
  - my-coder: My Coder ()
    Tags: ['coder', 'coding', 'custom', 'my']
Search "coder": ['my-coder']
```

### 本课成果

一个完整的人设解析和注册系统。带有 YAML frontmatter 的 markdown 文件被解析为
结构化的 `ExpertPersona` dataclass，支持双语章节提取。`ExpertRegistry` 提供
O(1) 的 slug 查找、部门分组以及跨名称、描述和自动提取标签的相关性评分全文搜索。

---
