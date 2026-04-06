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
