"""用户输入内容的提示词注入检测。

扫描文本中常见的注入模式：
  * 系统提示覆盖短语
  * 不可见 Unicode 字符
  * HTML 注释注入
  * 凭证窃取尝试
  * base64 编码的可疑载荷
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class InjectionWarning:
    """单条注入检测发现。"""
    category: str                     # 如 "override"、"unicode"、"exfiltration"
    description: str                  # 人类可读的说明
    severity: str                     # "LOW"、"MEDIUM"、"HIGH"
    span: tuple[int, int]            # (起始, 结束) 字符偏移量

# ── 不可见 Unicode 字符 ─────────────────────────────────
_INVISIBLE_CHARS: set[str] = {
    "\u200b",  # 零宽空格
    "\u200c",  # 零宽非连接符
    "\u200d",  # 零宽连接符
    "\u2060",  # 词连接符
    "\ufeff",  # 零宽不断空格 / BOM
    "\u202a",  # 从左到右嵌入
    "\u202b",  # 从右到左嵌入
    "\u202c",  # 弹出方向格式化
    "\u202d",  # 从左到右覆盖
    "\u202e",  # 从右到左覆盖
}

_INVISIBLE_RE = re.compile(
    "[" + "".join(re.escape(c) for c in sorted(_INVISIBLE_CHARS)) + "]"
)

# ── 系统提示覆盖模式（HIGH 严重级别） ─────────────
_OVERRIDE_PATTERNS: list[tuple[re.Pattern[str], str, str, str]] = [
    (re.compile(r"ignore\s+previous\s+instructions", re.IGNORECASE),
     "override", "System prompt override: 'ignore previous instructions'", "HIGH"),
    (re.compile(r"you\s+are\s+now", re.IGNORECASE),
     "override", "Identity reassignment: 'you are now'", "HIGH"),
    (re.compile(r"new\s+instructions\s*:", re.IGNORECASE),
     "override", "Injected instructions block", "HIGH"),
    (re.compile(r"(?:^|\s)system\s*:", re.IGNORECASE | re.MULTILINE),
     "override", "Fake system role prefix", "MEDIUM"),
    (re.compile(r"(?:^|\s)ADMIN\s*:", re.MULTILINE),
     "override", "Fake admin role prefix", "MEDIUM"),
    (re.compile(r"\[SYSTEM\]", re.IGNORECASE),
     "override", "Fake system tag: '[SYSTEM]'", "MEDIUM"),
]

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

# ── 凭证窃取模式 ─────────────────────────────────
_EXFIL_PATTERNS: list[tuple[re.Pattern[str], str, str, str]] = [
    (re.compile(r"https?://[^\s]+[?&](?:api_?key|token|secret|password)=", re.IGNORECASE),
     "exfiltration", "URL with API key/token query parameter", "HIGH"),
    (re.compile(r"curl\s+[^\n]*-H\s+['\"]?Authorization", re.IGNORECASE),
     "exfiltration", "curl command with Authorization header", "HIGH"),
]

_BASE64_RE = re.compile(r"[A-Za-z0-9+/]{32,}={0,2}")

_BASE64_SUSPICIOUS_PHRASES = [
    "ignore previous", "you are now", "system:", "new instructions",
    "ADMIN:", "/bin/sh", "exec(", "eval(",
]

class InjectionDetector:
    """扫描文本中的提示词注入尝试。"""

    def scan(self, text: str) -> list[InjectionWarning]:
        """返回在 *text* 中检测到的所有注入警告。"""
        warnings: list[InjectionWarning] = []

        # 1. 系统提示覆盖模式
        for pat, cat, desc, sev in _OVERRIDE_PATTERNS:
            for m in pat.finditer(text):
                warnings.append(InjectionWarning(cat, desc, sev, m.span()))

        # 2. 不可见 Unicode
        for m in _INVISIBLE_RE.finditer(text):
            char = m.group()
            warnings.append(InjectionWarning(
                "unicode",
                f"Invisible Unicode character U+{ord(char):04X}",
                "MEDIUM", m.span(),
            ))

        # 3. HTML 注释注入
        for m in _HTML_COMMENT_RE.finditer(text):
            warnings.append(InjectionWarning(
                "html_comment", "HTML comment injection", "MEDIUM", m.span(),
            ))

        # 4. 凭证窃取
        for pat, cat, desc, sev in _EXFIL_PATTERNS:
            for m in pat.finditer(text):
                warnings.append(InjectionWarning(cat, desc, sev, m.span()))

        # 5. base64 编码的可疑载荷
        for m in _BASE64_RE.finditer(text):
            try:
                decoded = base64.b64decode(m.group(), validate=True).decode(
                    "utf-8", errors="ignore"
                )
            except Exception:
                continue
            for phrase in _BASE64_SUSPICIOUS_PHRASES:
                if phrase.lower() in decoded.lower():
                    warnings.append(InjectionWarning(
                        "base64",
                        f"Base64 payload containing '{phrase}'",
                        "HIGH", m.span(),
                    ))
                    break

        return warnings

    def is_safe(self, text: str) -> bool:
        """当 *text* 不包含 HIGH 严重级别警告时返回 True。"""
        return all(w.severity != "HIGH" for w in self.scan(text))

    @staticmethod
    def sanitize(text: str) -> str:
        """从 *text* 中移除不可见 Unicode 字符。"""
        return _INVISIBLE_RE.sub("", text)
