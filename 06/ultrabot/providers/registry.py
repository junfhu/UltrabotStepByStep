# ultrabot/providers/registry.py
"""已知 LLM 提供者规格的静态注册表。

取自 ultrabot/providers/registry.py。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProviderSpec:
    """受支持 LLM 提供者的不可变描述符。

    取自 ultrabot/providers/registry.py 第 13-30 行。
    """
    name: str
    keywords: tuple[str, ...] = ()
    env_key: str = ""
    display_name: str = ""
    backend: str = "openai_compat"  # "openai_compat" | "anthropic"
    default_api_base: str = ""
    is_local: bool = False


# 规范提供者注册表（取自第 37-154 行）
PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        # `openai_compatible` 指任何兼容 OpenAI SDK / OpenAI API 的提供者。
        name="openai_compatible",
        keywords=("openai", "compatible", "gpt", "o1", "o3", "o4", "minimax"),
        env_key="OPENAI_API_KEY",
        display_name="OpenAI-Compatible",
        default_api_base="https://ark.cn-beijing.volces.com/api/coding/v3",
    ),
    ProviderSpec(
        name="anthropic",
        keywords=("anthropic", "claude"),
        env_key="ANTHROPIC_API_KEY",
        display_name="Anthropic",
        backend="anthropic",
        default_api_base="https://api.anthropic.com",
    ),
    ProviderSpec(
        name="deepseek",
        keywords=("deepseek",),
        env_key="DEEPSEEK_API_KEY",
        display_name="DeepSeek",
        default_api_base="https://api.deepseek.com/v1",
    ),
    ProviderSpec(
        name="groq",
        keywords=("groq",),
        env_key="GROQ_API_KEY",
        display_name="Groq",
        default_api_base="https://api.groq.com/openai/v1",
    ),
    ProviderSpec(
        name="ollama",
        keywords=("ollama",),
        display_name="Ollama (local)",
        default_api_base="http://localhost:11434/v1",
        is_local=True,
    ),
)


def find_by_name(name: str) -> ProviderSpec | None:
    """按名称查找提供者规格（不区分大小写）。"""
    for spec in PROVIDERS:
        if spec.name == name.lower():
            return spec
    return None


def find_by_keyword(keyword: str) -> ProviderSpec | None:
    """按关键词匹配查找提供者规格。"""
    kw = keyword.lower()
    for spec in PROVIDERS:
        if kw in spec.keywords:
            return spec
    return None
