# ultrabot/security/redact.py
"""基于正则表达式的凭证/密钥脱敏，用于日志和输出。"""

from __future__ import annotations

import re
from typing import Any

# ── 模式注册表：(名称, 已编译正则) ─────────────────────
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("openai_key",          re.compile(r"sk-[A-Za-z0-9_-]{10,}")),
    ("generic_key_prefix",  re.compile(r"key-[A-Za-z0-9_-]{10,}")),
    ("slack_token",         re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("github_pat_classic",  re.compile(r"ghp_[A-Za-z0-9]{10,}")),
    ("github_pat_fine",     re.compile(r"github_pat_[A-Za-z0-9_]{10,}")),
    ("aws_access_key",      re.compile(r"AKIA[A-Z0-9]{16}")),
    ("google_api_key",      re.compile(r"AIza[A-Za-z0-9_-]{30,}")),
    ("stripe_secret",       re.compile(r"sk_(?:live|test)_[A-Za-z0-9]{10,}")),
    ("sendgrid_key",        re.compile(r"SG\.[A-Za-z0-9_-]{10,}")),
    ("huggingface_token",   re.compile(r"hf_[A-Za-z0-9]{10,}")),
    ("bearer_token",
     re.compile(r"(Authorization:\s*Bearer\s+)(\S+)", re.IGNORECASE)),
    ("generic_secret_param",
     re.compile(r"((?:key|token|secret|password)\s*=\s*)([A-Za-z0-9+/=_-]{32,})",
                re.IGNORECASE)),
    ("email_password",
     re.compile(r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}):(\S+)")),
]


def redact(text: str) -> str:
    """将 *text* 中所有检测到的密钥替换为 [REDACTED]。"""
    if not text:
        return text
    for name, pattern in PATTERNS:
        if name == "bearer_token":
            text = pattern.sub(r"\1[REDACTED]", text)
        elif name == "generic_secret_param":
            text = pattern.sub(r"\1[REDACTED]", text)
        elif name == "email_password":
            text = pattern.sub(r"\1:[REDACTED]", text)
        else:
            text = pattern.sub("[REDACTED]", text)
    return text


class RedactingFilter:
    """对日志记录进行密钥脱敏的 loguru 过滤器。

    用法::
        from loguru import logger
        logger.add(sink, filter=RedactingFilter())
    """

    def __call__(self, record: dict[str, Any]) -> bool:
        if "message" in record:
            record["message"] = redact(record["message"])
        return True
