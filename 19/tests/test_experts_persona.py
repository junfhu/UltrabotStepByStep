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
