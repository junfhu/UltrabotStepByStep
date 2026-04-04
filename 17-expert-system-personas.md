# Ultrabot：30 课程开发指南
**从零开始构建一个生产级 AI 助手框架。**
本指南将带你从"向 LLM 问好"一步步走到一个完整的多提供者、多通道 AI 智能体，具备工具调用、记忆、安全防护和 Web 界面。每节课程都建立在上一节课的基础之上。每节课都包含可运行的代码和测试。  
本教程的主要思路来自于
- Nanobot (https://github.com/HKUDS/nanobot)
- Learn-Claude-Code (https://github.com/shareAI-lab/learn-claude-code/)

本课程设计由AI辅助下完成，因为课程自身也在不停修正，请参考 https://github.com/junfhu/UltrabotStepByStep，如果您觉得对您有帮助，请帮助点亮一颗星。  
本课程中使用的大模型提供商是火山引擎Code Plan，如果正好你也需要，可以使用我的邀请码获取9折优惠 https://volcengine.com/L/_01BJCkKdMc/  邀请码：HHCDB4J4）  



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

#: 随包分发的内置人设 markdown 文件路径。
BUNDLED_PERSONAS_DIR: Path = Path(__file__).parent / "personas"

__all__ = [
    "BUNDLED_PERSONAS_DIR",
    "ExpertPersona",
    "ExpertRegistry",
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

## 本课使用的 Python 知识

### `@dataclass` 与 `slots=True`（数据类）

`@dataclass` 装饰器自动为类生成 `__init__`、`__repr__`、`__eq__` 等方法，让你只需声明字段即可。加上 `slots=True` 参数会让 Python 使用 `__slots__` 而非字典来存储属性，减少每个实例的内存占用。

```python
from dataclasses import dataclass

@dataclass(slots=True)
class Person:
    name: str
    age: int = 0

p = Person(name="Alice", age=30)
print(p)  # Person(name='Alice', age=30)
```

**为什么在本课中使用：** `ExpertPersona` 是本课的核心数据结构，包含十几个字段。`@dataclass` 省去了手写冗长构造函数的工作。`slots=True` 在加载数百个专家人设时显著降低内存占用。

### `field(default_factory=list)`（数据类字段工厂）

在 `@dataclass` 中，可变类型（如列表、字典）的默认值不能直接写成 `tags: list = []`（会被所有实例共享），必须使用 `field(default_factory=list)` 为每个实例创建独立的副本。

```python
from dataclasses import dataclass, field

@dataclass
class Config:
    items: list[str] = field(default_factory=list)  # 每个实例独立的列表

a = Config()
b = Config()
a.items.append("x")
print(b.items)  # []  -- b 不受 a 的影响
```

**为什么在本课中使用：** `ExpertPersona` 的 `tags` 字段是一个列表，每个专家有自己独立的标签集合。使用 `field(default_factory=list)` 确保每个人设实例的标签列表互不干扰。

### `from pathlib import Path`（现代路径操作）

`pathlib.Path` 是 Python 3 引入的面向对象的文件路径操作接口，比传统的 `os.path` 字符串拼接更直观、更安全。

```python
from pathlib import Path

p = Path("/home/user/docs")
file = p / "notes.md"         # 用 / 拼接路径
text = file.read_text("utf-8")  # 直接读取文件
print(file.stem)               # "notes"（无扩展名的文件名）
print(file.parent.name)        # "docs"（父目录名）
```

**为什么在本课中使用：** 人设文件散布在目录树中，代码需要遍历目录（`rglob("*.md")`）、获取文件名（`path.stem` 作为 slug）、读取内容（`read_text`）。`Path` 让这些操作简洁又可读。

### `re.compile()` 与正则表达式

`re` 模块提供正则表达式支持。`re.compile()` 将正则模式预编译为对象，可重复使用以提高性能。`re.DOTALL` 标志让 `.` 也匹配换行符。

```python
import re

pattern = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
match = pattern.match(text)
if match:
    content = match.group(1)  # 提取第一个捕获组
```

**为什么在本课中使用：** 人设文件的 YAML frontmatter 被 `---` 分隔符包裹，正则表达式用于精确匹配和提取这部分内容。预编译的正则在反复解析数百个文件时比每次调用 `re.match()` 更高效。

### `@property`（属性装饰器）

`@property` 让方法像属性一样被访问，不需要加括号。常用于定义只读的计算属性。

```python
class User:
    def __init__(self, first, last):
        self.first = first
        self.last = last

    @property
    def full_name(self):
        return f"{self.first} {self.last}"

u = User("张", "三")
print(u.full_name)  # "张 三"，无需 u.full_name()
```

**为什么在本课中使用：** `ExpertPersona` 的 `system_prompt` 属性直接返回 `raw_body`，让调用者用 `persona.system_prompt` 获取 LLM 系统提示词，语义清晰且不暴露内部字段名。

### `frozenset`（不可变集合）

`frozenset` 是不可变的集合类型，创建后不能增删元素。适合用作常量集合或字典的键。

```python
STOP_WORDS = frozenset(["的", "了", "是", "在"])

if "的" in STOP_WORDS:  # O(1) 查找
    print("是停用词")
```

**为什么在本课中使用：** `_STOP_WORDS` 存储中文停用词（"的"、"了"、"是"等），在标签提取时需要快速判断字符是否应被排除。`frozenset` 的查找速度为 O(1)，且不可变保证了全局常量的安全性。

### `defaultdict(list)`（带默认值的字典）

`collections.defaultdict` 在访问不存在的键时自动创建默认值，省去了手动检查和初始化的代码。

```python
from collections import defaultdict

groups = defaultdict(list)
groups["fruits"].append("apple")   # 自动创建空列表
groups["fruits"].append("banana")
print(groups)  # {'fruits': ['apple', 'banana']}
```

**为什么在本课中使用：** `ExpertRegistry` 用 `defaultdict(list)` 建立部门索引——当注册新专家时，如果部门键不存在会自动创建空列表，代码更简洁，无需判断键是否存在。

### `tuple` 返回值（多值返回）

Python 函数可以返回一个元组来同时传递多个值，调用者通过解包赋值来接收。

```python
def parse(text):
    header = text[:10]
    body = text[10:]
    return header, body  # 返回元组

h, b = parse("HEADER____This is the body")  # 解包
```

**为什么在本课中使用：** `_parse_frontmatter()` 返回 `(meta, body)` 元组，将 frontmatter 元数据和 markdown 正文一次性返回。调用者用 `meta, body = _parse_frontmatter(text)` 优雅地获取两个结果。

### 字符串方法链（`splitlines`、`find`、`strip`）

Python 字符串提供丰富的内置方法：`splitlines()` 按行分割，`find()` 查找子串位置，`strip()` 去除首尾空白。

```python
text = "  key: value  "
colon = text.find(":")        # 5
key = text[:colon].strip()    # "key"
val = text[colon+1:].strip()  # "value"
```

**为什么在本课中使用：** YAML frontmatter 解析器逐行扫描内容，使用 `splitlines()` 分行、`find(":")` 定位键值分隔符、`strip()` 清理空白——完全不需要外部 YAML 库，保持依赖最小化。

### `sorted()` 与 `key=lambda`（自定义排序）

`sorted()` 返回排序后的新列表。`key` 参数接受一个函数，指定排序依据。`lambda` 是匿名函数的简写。

```python
students = [("Bob", 85), ("Alice", 92), ("Charlie", 78)]
by_score = sorted(students, key=lambda s: -s[1])  # 按分数降序
print(by_score)  # [('Alice', 92), ('Bob', 85), ('Charlie', 78)]
```

**为什么在本课中使用：** `ExpertRegistry` 的搜索功能计算每个人设的相关性分数，然后用 `sorted(scored, key=lambda x: -x[0])` 按分数降序排列，返回最匹配的结果。`list_all()` 用 `key=lambda p: (p.department, p.slug)` 先按部门再按 slug 双重排序。

### `__len__`、`__contains__`、`__repr__`（魔术方法）

Python 的魔术方法（也叫 dunder 方法）让你的自定义类可以与内置操作符和函数无缝配合。

```python
class MyList:
    def __init__(self):
        self._items = []

    def __len__(self):
        return len(self._items)  # 支持 len(obj)

    def __contains__(self, item):
        return item in self._items  # 支持 item in obj

    def __repr__(self):
        return f"<MyList size={len(self._items)}>"  # 支持 print(obj)
```

**为什么在本课中使用：** `ExpertRegistry` 实现了这三个魔术方法，让它可以像内置容器一样使用：`len(registry)` 获取专家数量、`"slug" in registry` 检查专家是否存在、`print(registry)` 显示友好的描述。

### `@staticmethod`（静态方法）

`@staticmethod` 定义不需要访问实例（`self`）或类（`cls`）的方法。它本质上是一个放在类命名空间中的普通函数。

```python
class MathHelper:
    @staticmethod
    def add(a, b):
        return a + b

print(MathHelper.add(1, 2))  # 3，不需要创建实例
```

**为什么在本课中使用：** `ExpertRegistry._score_match()` 是一个纯计算函数，只需要接收参数就能工作，不需要访问注册表实例。定义为 `@staticmethod` 清楚地表达了这个意图。

### `filter(None, iterable)`（过滤空值）

`filter(None, iterable)` 会过滤掉所有布尔值为假的元素（`None`、`""`、`0`、`[]` 等），只保留"真值"元素。

```python
values = ["hello", "", None, "world", ""]
cleaned = list(filter(None, values))
print(cleaned)  # ['hello', 'world']
```

**为什么在本课中使用：** `_extract_tags()` 用 `filter(None, [persona.name, persona.description, persona.department])` 将非空字段拼接成标签来源文本，自动跳过空字符串字段。

### CJK 字符正则匹配（Unicode 范围）

通过正则表达式的 Unicode 范围 `[\u4e00-\u9fff]` 可以匹配所有常见的中日韩汉字。

```python
import re

text = "Hello 前端开发者 World"
cjk_chunks = re.findall(r"[\u4e00-\u9fff]+", text)
print(cjk_chunks)  # ['前端开发者']
```

**为什么在本课中使用：** 专家人设包含大量中文内容，标签提取需要同时处理英文单词和中文字符。通过 `[\u4e00-\u9fff]+` 匹配中文片段，再生成单字和双字组（bigram），实现高效的中文搜索索引。

### `__all__`（模块导出控制）

在 `__init__.py` 中定义 `__all__` 列表，明确指定模块对外公开的名称。当用户执行 `from module import *` 时，只有 `__all__` 中列出的名称会被导入。

```python
# mypackage/__init__.py
from .core import Engine
from .utils import helper

__all__ = ["Engine"]  # from mypackage import * 只导入 Engine
```

**为什么在本课中使用：** `ultrabot/experts/__init__.py` 通过 `__all__` 明确导出 `ExpertPersona`、`ExpertRegistry` 等公共接口，同时隐藏内部实现细节，为使用者提供清晰的 API 边界。

### `pytest` 测试框架与 `@pytest.fixture`

`pytest` 是 Python 最流行的测试框架。`@pytest.fixture` 定义可复用的测试准备代码（如创建临时目录、初始化对象），测试函数通过参数名自动注入。

```python
import pytest

@pytest.fixture
def sample_data():
    return {"name": "test", "value": 42}

def test_name(sample_data):  # 自动注入 fixture
    assert sample_data["name"] == "test"
```

**为什么在本课中使用：** 测试用例需要反复创建 `ExpertRegistry` 并注册人设。`tmp_path` fixture 提供临时目录用于写入测试文件，避免测试之间的状态污染。`pytest` 的断言自动生成清晰的错误信息。
