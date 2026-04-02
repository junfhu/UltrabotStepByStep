# 课程 5：配置系统

**目标：** 使用 Pydantic、JSON 文件和环境变量覆盖构建一个完善的配置系统。

**你将学到：**
- 使用 Pydantic BaseSettings 进行类型化配置
- camelCase JSON 别名（Python 风格代码，漂亮的 JSON）
- 从文件加载配置并支持环境变量覆盖
- `~/.ultrabot/config.json` 模式

**新建文件：**
- `ultrabot/config/schema.py` -- Pydantic 配置模型
- `ultrabot/config/loader.py` -- 从 JSON 加载/保存配置
- `ultrabot/config/paths.py` -- 文件系统路径辅助函数
- `ultrabot/config/__init__.py` -- 公共导出

### 步骤 1：安装 Pydantic

```bash
pip install pydantic pydantic-settings
```

更新 `pyproject.toml`：

```toml
[project]
name = "ultrabot"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "openai>=1.0",
    "pydantic>=2.0",
    "pydantic-settings>=2.0",
]
```

### 步骤 2：定义配置模式

这取自 `ultrabot/config/schema.py`。关键洞察：每个 Pydantic 模型使用 `alias_generator=to_camel`，所以 Python 代码使用 `snake_case`，但 JSON 文件使用 `camelCase`：

```python
# ultrabot/config/schema.py
"""ultrabot 的 Pydantic 配置模式。

使用 camelCase JSON 别名，使配置文件看起来像：
  {"agents": {"defaults": {"contextWindowTokens": 200000}}}
而 Python 代码使用：
  config.agents.defaults.context_window_tokens

取自 ultrabot/config/schema.py。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from pydantic_settings import BaseSettings


# -- 带 camelCase 别名的基础模型 --

class Base(BaseModel):
    """所有配置段共享的基类。

    取自 ultrabot/config/schema.py 第 40-50 行。
    """
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )


# -- 提供者配置 --

class ProviderConfig(Base):
    """单个 LLM 提供者的配置。

    取自 ultrabot/config/schema.py 第 58-71 行。
    """
    api_key: str | None = Field(default=None, description="API key (prefer env vars).")
    api_base: str | None = Field(default=None, description="Base URL override.")
    enabled: bool = Field(default=True, description="Whether this provider is active.")
    priority: int = Field(default=100, description="Failover priority (lower = first).")


class ProvidersConfig(Base):
    """所有提供者插槽。

    取自 ultrabot/config/schema.py 第 74-89 行。
    """
    openai: ProviderConfig = Field(default_factory=ProviderConfig)
    anthropic: ProviderConfig = Field(default_factory=ProviderConfig)
    deepseek: ProviderConfig = Field(default_factory=ProviderConfig)
    groq: ProviderConfig = Field(default_factory=ProviderConfig)
    ollama: ProviderConfig = Field(
        default_factory=lambda: ProviderConfig(api_base="http://localhost:11434/v1")
    )


# -- 智能体默认值 --

class AgentDefaults(Base):
    """智能体的默认参数。

    取自 ultrabot/config/schema.py 第 97-112 行。
    """
    model: str = Field(default="gpt-4o-mini", description="Default model identifier.")
    provider: str = Field(default="openai", description="Default provider name.")
    max_tokens: int = Field(default=16384, description="Max tokens per completion.")
    context_window_tokens: int = Field(default=200000, description="Context window size.")
    temperature: float = Field(default=0.5, ge=0.0, le=2.0)
    max_tool_iterations: int = Field(default=10, description="Tool-use loop limit.")
    timezone: str = Field(default="UTC", description="IANA timezone.")


class AgentsConfig(Base):
    """智能体相关配置。"""
    defaults: AgentDefaults = Field(default_factory=AgentDefaults)


# -- 工具配置 --

class ExecToolConfig(Base):
    """Shell 执行安全防护。"""
    enable: bool = Field(default=True)
    timeout: int = Field(default=120, description="Per-command timeout in seconds.")


class ToolsConfig(Base):
    """工具的聚合配置。"""
    exec: ExecToolConfig = Field(default_factory=ExecToolConfig)
    restrict_to_workspace: bool = Field(default=True)


# -- 根配置 --

class Config(BaseSettings):
    """ultrabot 的根配置对象。

    继承自 BaseSettings，因此每个字段都可以通过
    以 ULTRABOT_ 为前缀的环境变量来覆盖。

    示例：ULTRABOT_AGENTS__DEFAULTS__MODEL=gpt-4o

    取自 ultrabot/config/schema.py 第 309-388 行。
    """
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        env_prefix="ULTRABOT_",
        env_nested_delimiter="__",
    )

    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)

    def get_provider(self, model: str | None = None) -> str:
        """从模型字符串中解析出提供者名称。

        取自 ultrabot/config/schema.py 第 335-362 行。
        """
        if model is None:
            return self.agents.defaults.provider

        keywords = {
            "anthropic": ["claude", "anthropic"],
            "openai": ["gpt", "o1", "o3", "o4"],
            "deepseek": ["deepseek"],
            "groq": ["groq", "llama"],
            "ollama": ["ollama"],
        }
        model_lower = model.lower()
        for provider_name, kws in keywords.items():
            for kw in kws:
                if kw in model_lower:
                    prov = getattr(self.providers, provider_name, None)
                    if prov and prov.enabled:
                        return provider_name

        return self.agents.defaults.provider

    def get_api_key(self, provider: str | None = None) -> str | None:
        """返回指定提供者的 API 密钥。"""
        name = provider or self.agents.defaults.provider
        prov = getattr(self.providers, name, None)
        return prov.api_key if prov else None
```

### 步骤 3：构建配置加载器

```python
# ultrabot/config/loader.py
"""配置加载和保存。

规范路径为 ~/.ultrabot/config.json。

取自 ultrabot/config/loader.py。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ultrabot.config.schema import Config


def get_config_path() -> Path:
    """返回默认配置文件路径：~/.ultrabot/config.json。

    取自 ultrabot/config/loader.py 第 39-56 行。
    """
    import os

    env = os.environ.get("ULTRABOT_CONFIG")
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".ultrabot" / "config.json"


def load_config(path: str | Path | None = None) -> Config:
    """加载 ultrabot 配置。

    1. 读取 path（或默认路径）处的 JSON 文件。
    2. 与环境变量覆盖合并（由 pydantic-settings 处理）。
    3. 如果文件不存在则创建默认配置。

    取自 ultrabot/config/loader.py 第 85-115 行。
    """
    resolved = Path(path).expanduser().resolve() if path else get_config_path()

    file_data: dict[str, Any] = {}
    if resolved.is_file():
        try:
            text = resolved.read_text(encoding="utf-8")
            file_data = json.loads(text)
        except json.JSONDecodeError:
            file_data = {}
    else:
        resolved.parent.mkdir(parents=True, exist_ok=True)

    # pydantic-settings 在文件数据之上合并环境变量
    config = Config(**file_data)

    # 写入默认值，让用户有一个起始模板
    if not resolved.is_file():
        save_config(config, resolved)

    return config


def save_config(config: Config, path: str | Path | None = None) -> None:
    """将配置序列化为 JSON 文件。

    取自 ultrabot/config/loader.py 第 118-140 行。
    """
    resolved = Path(path).expanduser().resolve() if path else get_config_path()
    resolved.parent.mkdir(parents=True, exist_ok=True)

    payload = config.model_dump(
        mode="json",
        by_alias=True,      # 在 JSON 中使用 camelCase 键
        exclude_none=True,
    )

    tmp = resolved.with_suffix(".tmp")
    try:
        tmp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        tmp.replace(resolved)  # 原子重命名
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
```

### 步骤 4：路径辅助函数

```python
# ultrabot/config/paths.py
"""文件系统路径辅助函数。

所有目录在首次访问时延迟创建。

取自 ultrabot/config/paths.py。
"""
from __future__ import annotations

from pathlib import Path

_DATA_DIR_NAME = ".ultrabot"


def _ensure_dir(path: Path) -> Path:
    """如果需要则创建路径及其父目录，然后返回它。"""
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_data_dir() -> Path:
    """~/.ultrabot -- 首次访问时创建。"""
    return _ensure_dir(Path.home() / _DATA_DIR_NAME)


def get_workspace_path(workspace: str | None = None) -> Path:
    """解析并返回工作区目录。"""
    if workspace is None:
        return _ensure_dir(get_data_dir() / "workspace")
    return _ensure_dir(Path(workspace).expanduser().resolve())


def get_cli_history_path() -> Path:
    """~/.ultrabot/cli_history。"""
    return get_data_dir() / "cli_history"
```

### 步骤 5：公共导出

```python
# ultrabot/config/__init__.py
"""配置子系统的公共接口。

取自 ultrabot/config/__init__.py。
"""
from ultrabot.config.loader import get_config_path, load_config, save_config
from ultrabot.config.paths import get_data_dir, get_workspace_path, get_cli_history_path
from ultrabot.config.schema import (
    Config, ProviderConfig, ProvidersConfig,
    AgentDefaults, AgentsConfig, ToolsConfig,
)

__all__ = [
    "Config", "ProviderConfig", "ProvidersConfig",
    "AgentDefaults", "AgentsConfig", "ToolsConfig",
    "get_config_path", "load_config", "save_config",
    "get_data_dir", "get_workspace_path", "get_cli_history_path",
]
```

### 测试

```python
# tests/test_session5.py
"""课程 5 的测试 -- 配置系统。"""
import json
import pytest
from pathlib import Path


def test_config_defaults():
    """Config() 创建合理的默认值。"""
    from ultrabot.config.schema import Config

    cfg = Config()
    assert cfg.agents.defaults.model == "gpt-4o-mini"
    assert cfg.agents.defaults.temperature == 0.5
    assert cfg.agents.defaults.max_tool_iterations == 10


def test_config_from_dict():
    """Config 可以从字典初始化（模拟 JSON 加载）。"""
    from ultrabot.config.schema import Config

    cfg = Config(**{
        "agents": {"defaults": {"model": "gpt-4o", "temperature": 0.8}},
    })
    assert cfg.agents.defaults.model == "gpt-4o"
    assert cfg.agents.defaults.temperature == 0.8


def test_config_camel_case_aliases():
    """Config 接受来自 JSON 的 camelCase 键。"""
    from ultrabot.config.schema import Config

    cfg = Config(**{
        "agents": {"defaults": {"maxToolIterations": 20, "contextWindowTokens": 100000}},
    })
    assert cfg.agents.defaults.max_tool_iterations == 20
    assert cfg.agents.defaults.context_window_tokens == 100000


def test_config_serialization():
    """Config 序列化为 camelCase JSON。"""
    from ultrabot.config.schema import Config

    cfg = Config()
    payload = cfg.model_dump(mode="json", by_alias=True, exclude_none=True)

    # 检查使用了 camelCase 别名
    assert "agents" in payload
    defaults = payload["agents"]["defaults"]
    assert "maxToolIterations" in defaults
    assert "contextWindowTokens" in defaults


def test_get_provider():
    """get_provider() 将模型名称解析为提供者名称。"""
    from ultrabot.config.schema import Config

    cfg = Config()
    assert cfg.get_provider("gpt-4o") == "openai"
    assert cfg.get_provider("claude-3-opus") == "anthropic"
    assert cfg.get_provider("deepseek-r1") == "deepseek"
    assert cfg.get_provider(None) == cfg.agents.defaults.provider


def test_load_save_config(tmp_path):
    """load_config 和 save_config 能正确往返。"""
    from ultrabot.config.loader import load_config, save_config
    from ultrabot.config.schema import Config

    cfg_path = tmp_path / "config.json"

    # 首次加载会创建默认文件
    cfg = load_config(cfg_path)
    assert cfg_path.exists()

    # 修改并保存
    cfg.agents.defaults.model = "gpt-4o"
    save_config(cfg, cfg_path)

    # 重新加载并验证
    cfg2 = load_config(cfg_path)
    assert cfg2.agents.defaults.model == "gpt-4o"


def test_env_var_override(monkeypatch):
    """环境变量覆盖配置文件值。"""
    from ultrabot.config.schema import Config

    monkeypatch.setenv("ULTRABOT_AGENTS__DEFAULTS__MODEL", "o1-preview")
    cfg = Config()
    assert cfg.agents.defaults.model == "o1-preview"
```

### 检查点

创建配置文件：

```bash
mkdir -p ~/.ultrabot
cat > ~/.ultrabot/config.json << 'EOF'
{
  "providers": {
    "openai": {
      "enabled": true,
      "priority": 1
    }
  },
  "agents": {
    "defaults": {
      "model": "gpt-4o-mini",
      "temperature": 0.7,
      "maxToolIterations": 15
    }
  }
}
EOF
```

测试：

```python
from ultrabot.config import load_config
cfg = load_config()
print(f"Model: {cfg.agents.defaults.model}")
print(f"Temperature: {cfg.agents.defaults.temperature}")
print(f"Max iterations: {cfg.agents.defaults.max_tool_iterations}")
```

使用环境变量覆盖：

```bash
ULTRABOT_AGENTS__DEFAULTS__MODEL=gpt-4o python -c "
from ultrabot.config import load_config
cfg = load_config()
print(f'Model: {cfg.agents.defaults.model}')
"
# 输出：Model: gpt-4o
```

### 本课成果

一个使用 Pydantic BaseSettings 的类型化配置系统，具备：
- camelCase JSON 别名，使配置文件更美观
- 通过 `ULTRABOT_` 前缀的环境变量覆盖
- 自动创建默认文件
- 从模型名称自动检测提供者

---
