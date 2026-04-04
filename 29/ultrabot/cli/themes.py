# ultrabot/cli/themes.py  （关键摘录）

@dataclass
class ThemeColors:
    primary: str = "blue"
    secondary: str = "cyan"
    success: str = "green"
    warning: str = "yellow"
    error: str = "red"

@dataclass
class Theme:
    name: str
    description: str = ""
    colors: ThemeColors = field(default_factory=ThemeColors)
    spinner: ThemeSpinner = field(default_factory=ThemeSpinner)
    branding: ThemeBranding = field(default_factory=ThemeBranding)

# 内置主题：default（蓝/青）、dark（绿）、light（明亮）、mono（灰度）
_BUILTIN_THEMES = {"default": THEME_DEFAULT, "dark": THEME_DARK,
                    "light": THEME_LIGHT, "mono": THEME_MONO}

class ThemeManager:
    def __init__(self, themes_dir: Path | None = None):
        self._builtin = dict(_BUILTIN_THEMES)
        self._user: dict[str, Theme] = {}
        self._active = self._builtin["default"]
        if themes_dir:
            self.load_user_themes()

    def set_active(self, name: str) -> bool:
        theme = self.get(name)
        if theme is None:
            return False
        self._active = theme
        return True
