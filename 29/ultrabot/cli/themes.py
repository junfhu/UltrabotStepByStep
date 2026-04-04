# ultrabot/cli/themes.py
"""CLI 主题管理 -- 内置主题和用户自定义主题。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class ThemeColors:
    """主题颜色配置。"""
    primary: str = "blue"
    secondary: str = "cyan"
    success: str = "green"
    warning: str = "yellow"
    error: str = "red"


@dataclass
class ThemeSpinner:
    """主题 spinner 配置。"""
    style: str = "dots"
    color: str = "blue"


@dataclass
class ThemeBranding:
    """主题品牌配置。"""
    logo: str = "🤖"
    name: str = "Ultrabot"


@dataclass
class Theme:
    """完整的主题定义。"""
    name: str
    description: str = ""
    colors: ThemeColors = field(default_factory=ThemeColors)
    spinner: ThemeSpinner = field(default_factory=ThemeSpinner)
    branding: ThemeBranding = field(default_factory=ThemeBranding)


# ── 内置主题 ───────────────────────────────────────

THEME_DEFAULT = Theme(
    name="default",
    description="Default blue/cyan theme",
    colors=ThemeColors(primary="blue", secondary="cyan"),
)

THEME_DARK = Theme(
    name="dark",
    description="Dark green theme",
    colors=ThemeColors(primary="green", secondary="dark_green"),
    spinner=ThemeSpinner(style="dots", color="green"),
)

THEME_LIGHT = Theme(
    name="light",
    description="Light bright theme",
    colors=ThemeColors(primary="bright_blue", secondary="bright_cyan"),
)

THEME_MONO = Theme(
    name="mono",
    description="Monochrome grayscale theme",
    colors=ThemeColors(primary="white", secondary="bright_black",
                       success="white", warning="white", error="white"),
    spinner=ThemeSpinner(style="line", color="white"),
)

_BUILTIN_THEMES = {
    "default": THEME_DEFAULT,
    "dark": THEME_DARK,
    "light": THEME_LIGHT,
    "mono": THEME_MONO,
}


class ThemeManager:
    """管理内置和用户自定义主题。"""

    def __init__(self, themes_dir: Path | None = None):
        self._builtin: dict[str, Theme] = dict(_BUILTIN_THEMES)
        self._user: dict[str, Theme] = {}
        self._active: Theme = self._builtin["default"]
        if themes_dir:
            self._load_user_themes(themes_dir)

    @property
    def active(self) -> Theme:
        """当前激活的主题。"""
        return self._active

    def list_themes(self) -> list[Theme]:
        """列出所有可用主题。"""
        all_themes = {**self._builtin, **self._user}
        return list(all_themes.values())

    def get(self, name: str) -> Theme | None:
        """按名称获取主题。"""
        if name in self._user:
            return self._user[name]
        return self._builtin.get(name)

    def set_active(self, name: str) -> bool:
        """设置激活的主题。成功返回 True，主题不存在返回 False。"""
        theme = self.get(name)
        if theme is None:
            logger.warning("Theme '{}' not found", name)
            return False
        self._active = theme
        logger.info("Active theme set to '{}'", name)
        return True

    def _load_user_themes(self, themes_dir: Path) -> None:
        """从目录加载用户自定义主题。"""
        if not themes_dir.exists():
            return
        for f in themes_dir.glob("*.json"):
            try:
                import json
                data = json.loads(f.read_text(encoding="utf-8"))
                theme = Theme(
                    name=data.get("name", f.stem),
                    description=data.get("description", ""),
                    colors=ThemeColors(**data.get("colors", {})),
                )
                self._user[theme.name] = theme
                logger.debug("Loaded user theme: {}", theme.name)
            except Exception as e:
                logger.warning("Failed to load theme from {}: {}", f, e)
