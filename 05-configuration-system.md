# Agent: 30课程开发指南
**从零开始构建一个生产级 AI 助手框架。**
本指南将带你从"向 LLM 问好"一步步走到一个完整的多提供者、多通道 AI 智能体，具备工具调用、记忆、安全防护和 Web 界面。每节课程都建立在上一节课的基础之上。每节课都包含可运行的代码和测试。  
本教程的主要思路来自于
- Nanobot (https://github.com/HKUDS/nanobot)
- Learn-Claude-Code (https://github.com/shareAI-lab/learn-claude-code/)

本课程设计由AI辅助下完成，因为课程自身也在不停修正，请参考 https://github.com/junfhu/UltrabotStepByStep，如果您觉得对您有帮助，请帮助点亮一颗星。  



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

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
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
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource


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
    # `openai` 表示 OpenAI 官方 API（api.openai.com）。
    openai: ProviderConfig = Field(default_factory=ProviderConfig)
    # `openai_compatible` 表示兼容 OpenAI SDK / OpenAI API 格式的第三方提供者。
    openai_compatible: ProviderConfig = Field(default_factory=ProviderConfig)
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
    model: str = Field(default="minimax-m2.5", description="Default model identifier.")
    provider: str = Field(default="openai_compatible", description="Default provider name.")
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

    优先级（从高到低）：环境变量 > 配置文件（init kwargs） > 默认值。
    通过 settings_customise_sources 将环境变量的优先级提升到 init kwargs 之上，
    这样 load_config() 传入的文件数据不会覆盖环境变量。

    示例：ULTRABOT_AGENTS__DEFAULTS__MODEL=gpt-4o

    取自 ultrabot/config/schema.py 第 309-388 行。
    """
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        env_prefix="ULTRABOT_",
        env_nested_delimiter="__",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """环境变量优先于配置文件（init kwargs）。

        默认顺序是 init > env > dotenv > secrets。
        我们把 env 提到最前面，这样 ULTRABOT_* 环境变量
        可以覆盖 load_config() 从 JSON 文件读取后传入的值。
        """
        return (env_settings, init_settings, dotenv_settings, file_secret_settings)

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
            "openai": ["gpt", "o1", "o3", "o4"],
            "anthropic": ["claude", "anthropic"],
            "openai_compatible": ["minimax"],
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
    2. 将文件数据作为 init kwargs 传给 Config()。
    3. 环境变量覆盖文件数据（由 settings_customise_sources 保证优先级）。
    4. 如果文件不存在则创建默认配置。

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

    # file_data 作为 init kwargs 传入；
    # settings_customise_sources 保证 env vars 优先于 init kwargs
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
    assert cfg.agents.defaults.model == "minimax-m2.5"
    assert cfg.agents.defaults.temperature == 0.5
    assert cfg.agents.defaults.max_tool_iterations == 10


def test_config_from_dict():
    """Config 可以从字典初始化（模拟 JSON 加载）。"""
    from ultrabot.config.schema import Config

    cfg = Config(**{
        "agents": {"defaults": {"model": "minimax-m2.5", "temperature": 0.8}},
    })
    assert cfg.agents.defaults.model == "minimax-m2.5"
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
    assert cfg.get_provider("o3-mini") == "openai"
    assert cfg.get_provider("minimax-m2.5") == "openai_compatible"
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
    cfg.agents.defaults.model = "minimax-m2.5"
    save_config(cfg, cfg_path)

    # 重新加载并验证
    cfg2 = load_config(cfg_path)
    assert cfg2.agents.defaults.model == "minimax-m2.5"


def test_env_var_override(monkeypatch):
    """环境变量覆盖配置文件值（init kwargs）。"""
    from ultrabot.config.schema import Config

    monkeypatch.setenv("ULTRABOT_AGENTS__DEFAULTS__MODEL", "gpt-4o")
    # 模拟 load_config：文件数据通过 init kwargs 传入
    cfg = Config(**{
        "agents": {"defaults": {"model": "minimax-m2.5"}},
    })
    # 环境变量优先于 init kwargs
    assert cfg.agents.defaults.model == "gpt-4o"
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
      "priority": 2
    },
    "openaiCompatible": {
      "enabled": true,
      "priority": 1,
      "apiBase": "https://ark.cn-beijing.volces.com/api/coding/v3"
    }
  },
  "agents": {
    "defaults": {
      "model": "minimax-m2.5",
      "provider": "openai_compatible",
      "temperature": 0.7,
      "maxToolIterations": 15
    }
  }
}
EOF
```

测试：(确保已通过 `pip install -e .` 安装包)

```python
from ultrabot.config import load_config
cfg = load_config()
print(f"Model: {cfg.agents.defaults.model}")
print(f"Temperature: {cfg.agents.defaults.temperature}")
print(f"Max iterations: {cfg.agents.defaults.max_tool_iterations}")
```

使用 `ULTRABOT_` 前缀的环境变量覆盖配置文件中的值：

```bash
ULTRABOT_AGENTS__DEFAULTS__MODEL=gpt-4o \
python -c "
from ultrabot.config import load_config
cfg = load_config()
print(f'Model: {cfg.agents.defaults.model}')
print(f'Provider: {cfg.get_provider(cfg.agents.defaults.model)}')
"
# 输出：
# Model: gpt-4o
# Provider: openai
```

> **注意：** 课程 1-4 中使用的 `MODEL`、`OPENAI_BASE_URL`、`OPENAI_API_KEY` 环境变量是直接传给 OpenAI 客户端的，它们**不会**被 Pydantic BaseSettings 自动读取。配置系统使用 `ULTRABOT_` 前缀 + 双下划线分隔嵌套层级，例如 `ULTRABOT_AGENTS__DEFAULTS__MODEL`。后续课程中我们会将两者整合起来。

### 本课成果

一个使用 Pydantic BaseSettings 的类型化配置系统，具备 camelCase JSON 别名（使配置文件更美观）、通过 `ULTRABOT_` 前缀的环境变量覆盖、自动创建默认文件，以及从模型名称自动检测提供者。这直接对应 `ultrabot/config/` 子包。

---

## 本课使用的 Python 知识

### `pydantic.BaseModel`（Pydantic 数据模型）

Pydantic 的 `BaseModel` 提供了**带自动类型验证**的数据模型。定义时像普通类一样声明字段和类型，创建实例时 Pydantic 会自动验证和转换数据：

```python
from pydantic import BaseModel

class AgentDefaults(BaseModel):
    model: str = "gpt-4o-mini"
    temperature: float = 0.5
    max_tokens: int = 16384

cfg = AgentDefaults(temperature="0.8")   # 字符串 "0.8" 自动转为 float 0.8
print(cfg.temperature)                    # 0.8 (float 类型)
print(cfg.model)                          # gpt-4o-mini (使用默认值)

# cfg = AgentDefaults(temperature="abc")  # 报错！无法转换为 float
```

与 `@dataclass` 的区别：Pydantic 会**自动验证类型、转换数据、报告错误**，而 `@dataclass` 只是生成方法，不做验证。

**为什么在本课使用：** 配置数据来自 JSON 文件和环境变量（都是字符串），需要自动类型转换和验证。Pydantic 确保 `temperature` 一定是合法的浮点数、`max_tokens` 一定是整数，避免运行时因类型错误而崩溃。

---

### `pydantic_settings.BaseSettings`（Pydantic Settings）

`BaseSettings` 继承自 `BaseModel`，额外支持**从环境变量自动读取配置值**：

```python
from pydantic_settings import BaseSettings
from pydantic import ConfigDict

class Config(BaseSettings):
    model_config = ConfigDict(
        env_prefix="ULTRABOT_",           # 环境变量前缀
        env_nested_delimiter="__",         # 嵌套层级分隔符
    )
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
```

设置环境变量 `ULTRABOT_AGENTS__DEFAULTS__MODEL=gpt-4o` 后，`Config()` 会自动读取并设置 `config.agents.defaults.model = "gpt-4o"`。

**为什么在本课使用：** 配置需要支持"文件 + 环境变量"两种来源，且环境变量优先于文件。`BaseSettings` 让这个需求只需几行代码就能实现，不需要手动解析环境变量。

---

### `pydantic.Field()`（字段定义）

`Field()` 用于为 Pydantic 模型字段设置默认值、描述、验证约束等：

```python
from pydantic import Field

class AgentDefaults(BaseModel):
    model: str = Field(default="gpt-4o-mini", description="Default model identifier.")
    temperature: float = Field(default=0.5, ge=0.0, le=2.0)
    max_tokens: int = Field(default=16384, description="Max tokens per completion.")
```

- `default=`：默认值
- `description=`：字段描述（用于文档和自动生成 JSON Schema）
- `ge=0.0`：大于等于 0.0（greater than or equal）
- `le=2.0`：小于等于 2.0（less than or equal）

```python
# cfg = AgentDefaults(temperature=3.0)  # 报错！3.0 > 2.0，不满足 le=2.0
```

**为什么在本课使用：** `temperature` 的有效范围是 0.0 到 2.0，用 `ge` 和 `le` 在创建配置时就能发现错误，而不是等到调用 API 时才被拒绝。

---

### `default_factory`（默认值工厂函数）

`Field(default_factory=...)` 让每个实例获得一个**独立的**默认值对象：

```python
from pydantic import Field

class ProvidersConfig(BaseModel):
    openai: ProviderConfig = Field(default_factory=ProviderConfig)
    ollama: ProviderConfig = Field(
        default_factory=lambda: ProviderConfig(api_base="http://localhost:11434/v1")
    )
```

当 `default_factory` 需要传参数时，可以用 `lambda` 包装。

**为什么在本课使用：** 每个提供者配置（`ProviderConfig`）都是独立的对象。用 `default_factory` 确保 `openai` 和 `anthropic` 的配置互不影响。`ollama` 还需要自定义的 `api_base` 默认值，用 `lambda` 实现。

---

### `ConfigDict` 和 `alias_generator=to_camel`（别名生成器）

Pydantic 的 `ConfigDict` 配置模型行为。`alias_generator` 自动为每个字段生成别名：

```python
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

class Base(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,      # snake_case -> camelCase 别名
        populate_by_name=True,         # 同时接受原名和别名
    )

class AgentDefaults(Base):
    max_tool_iterations: int = 10      # Python 中用 snake_case
    context_window_tokens: int = 200000

cfg = AgentDefaults(**{"maxToolIterations": 20})  # JSON 中用 camelCase
print(cfg.max_tool_iterations)                     # 20
```

`to_camel` 将 `max_tool_iterations` 转为 `maxToolIterations`。

**为什么在本课使用：** Python 代码约定用 `snake_case`，JSON 配置文件约定用 `camelCase`。别名生成器让两边各自使用自然的命名风格，而不需要手动维护映射关系。

---

### `@classmethod`（类方法）

`@classmethod` 标记的方法属于**类本身**而不是实例，第一个参数是类 `cls` 而非实例 `self`：

```python
class Config(BaseSettings):
    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        # 调整配置源的优先级顺序
        return (env_settings, init_settings, dotenv_settings, file_secret_settings)
```

**为什么在本课使用：** Pydantic Settings 通过 `settings_customise_sources` 类方法让我们自定义配置源的优先级。将 `env_settings` 放在 `init_settings` 前面，确保环境变量能覆盖 JSON 文件中的值。这是框架提供的扩展点。

---

### 类继承链

多个类通过继承形成层级关系，子类自动获得父类的所有功能：

```python
class Base(BaseModel):            # 通用配置基类（含 camelCase 别名）
    model_config = ConfigDict(alias_generator=to_camel, ...)

class ProviderConfig(Base):        # 继承 Base 的别名功能
    api_key: str | None = None
    enabled: bool = True

class ProvidersConfig(Base):       # 继承 Base 的别名功能
    openai: ProviderConfig = ...

class Config(BaseSettings):        # 继承 BaseSettings（环境变量支持）
    providers: ProvidersConfig = ...
```

**为什么在本课使用：** `Base` 类统一设置了 `alias_generator=to_camel`，所有子配置类（`ProviderConfig`、`AgentDefaults` 等）继承这个设置，不需要每个类重复配置。`Config` 继承 `BaseSettings` 获得环境变量支持。继承链让配置系统层层叠加功能。

---

### `getattr()` 动态属性访问

`getattr(obj, name)` 通过字符串名称访问对象的属性：

```python
class ProvidersConfig:
    openai = ProviderConfig()
    anthropic = ProviderConfig()

providers = ProvidersConfig()
# 等价于 providers.openai
prov = getattr(providers, "openai")

# 动态访问：provider_name 是一个变量
provider_name = "anthropic"
prov = getattr(providers, provider_name, None)   # 不存在时返回 None
```

**为什么在本课使用：** `get_provider()` 和 `get_api_key()` 方法接收提供者名称字符串（如 `"openai"`、`"anthropic"`），需要用 `getattr(self.providers, name)` 动态查找对应的配置对象。这比写一堆 `if name == "openai": ...` 更简洁、更易扩展。

---

### `pathlib.Path` 高级操作

本课使用了 `pathlib.Path` 的多种高级方法：

```python
from pathlib import Path

# 获取用户主目录
home = Path.home()                     # 例如 /home/user

# 路径拼接
config_path = home / ".ultrabot" / "config.json"

# 创建目录（parents=True 递归创建，exist_ok=True 已存在不报错）
config_path.parent.mkdir(parents=True, exist_ok=True)

# 原子写入：先写临时文件，再重命名
tmp = config_path.with_suffix(".tmp")  # config.tmp
tmp.write_text('{"key": "value"}', encoding="utf-8")
tmp.replace(config_path)               # 原子重命名

# 删除文件（missing_ok=True 不存在不报错）
tmp.unlink(missing_ok=True)
```

**为什么在本课使用：** 配置文件系统需要创建 `~/.ultrabot/` 目录、读写 `config.json`、处理路径解析。原子写入（先写 `.tmp` 再 `replace`）确保即使程序中途崩溃，也不会留下损坏的配置文件。

---

### `__all__`（模块公共接口）

`__all__` 是一个列表，声明模块中哪些名称是"公开的"：

```python
# ultrabot/config/__init__.py
from ultrabot.config.schema import Config, ProviderConfig, ...
from ultrabot.config.loader import load_config, save_config, ...

__all__ = [
    "Config", "ProviderConfig", "ProvidersConfig",
    "load_config", "save_config",
    "get_data_dir", "get_workspace_path",
    ...
]
```

**为什么在本课使用：** `__all__` 告诉 IDE 和使用者"这些是你应该使用的公共 API"。当别人写 `from ultrabot.config import *` 时，只有 `__all__` 中列出的名称会被导入。它是模块公共接口的"目录"。

---

### `json.loads()` 和 `json.dumps()`（JSON 序列化）

JSON 是配置文件的常用格式，Python 通过 `json` 模块处理：

```python
import json

# 读取 JSON 文件
text = Path("config.json").read_text(encoding="utf-8")
data = json.loads(text)          # JSON 字符串 -> Python 字典

# 写入 JSON 文件
payload = {"agents": {"defaults": {"model": "gpt-4o"}}}
json_str = json.dumps(payload, indent=2, ensure_ascii=False)
# indent=2: 美化输出，缩进 2 格
# ensure_ascii=False: 保留中文等非 ASCII 字符
```

**为什么在本课使用：** 配置文件 `~/.ultrabot/config.json` 是 JSON 格式。`load_config()` 用 `json.loads()` 读取文件，`save_config()` 用 `json.dumps()` 写入文件。`indent=2` 让配置文件人类可读，`ensure_ascii=False` 支持中文等国际化内容。

---

### `model_dump()`（Pydantic 序列化）

Pydantic 模型的 `model_dump()` 方法将对象转为字典，支持多种选项：

```python
config = Config()
payload = config.model_dump(
    mode="json",           # 值转为 JSON 兼容类型
    by_alias=True,         # 使用 camelCase 别名作为键
    exclude_none=True,     # 排除值为 None 的字段
)
# payload 是一个可以直接 json.dumps() 的字典
```

**为什么在本课使用：** 保存配置时，需要将 Pydantic 模型转为 JSON 兼容的字典。`by_alias=True` 确保 JSON 文件使用 `camelCase` 键（如 `"maxToolIterations"` 而非 `"max_tool_iterations"`），`exclude_none=True` 让输出更简洁。

---

### `try` / `except` / `finally` + `raise`（异常处理）

完整的异常处理链，包括清理操作和异常重新抛出：

```python
tmp = resolved.with_suffix(".tmp")
try:
    tmp.write_text(json_str, encoding="utf-8")
    tmp.replace(resolved)         # 原子重命名
except Exception:
    tmp.unlink(missing_ok=True)   # 出错时清理临时文件
    raise                          # 重新抛出异常（不吞掉错误）
```

- `except Exception:` 捕获所有异常
- `tmp.unlink(missing_ok=True)` 清理临时文件
- `raise` 重新抛出异常，让调用者知道发生了错误

**为什么在本课使用：** 原子写入模式中，如果 `write_text()` 或 `replace()` 失败，临时文件不应该留在磁盘上。`except` 块负责清理，`raise` 确保错误不被静默吞掉。

---

### `tmp_path`（pytest 临时路径 fixture）

`tmp_path` 是 pytest 内置的 fixture，提供一个测试专用的临时目录：

```python
def test_load_save_config(tmp_path):
    cfg_path = tmp_path / "config.json"    # 临时目录下的文件路径
    cfg = load_config(cfg_path)
    assert cfg_path.exists()
    # 测试结束后 tmp_path 自动清理
```

**为什么在本课使用：** 测试配置文件的加载和保存功能时，不能操作真实的 `~/.ultrabot/config.json`（会影响用户配置）。`tmp_path` 提供隔离的临时目录，测试结束后自动清理，不会留下垃圾文件。

---

### `monkeypatch.setenv()`（模拟环境变量）

pytest 的 `monkeypatch` fixture 用于在测试中临时设置环境变量：

```python
def test_env_var_override(monkeypatch):
    monkeypatch.setenv("ULTRABOT_AGENTS__DEFAULTS__MODEL", "gpt-4o")
    cfg = Config(**{"agents": {"defaults": {"model": "minimax-m2.5"}}})
    assert cfg.agents.defaults.model == "gpt-4o"  # 环境变量优先
```

测试结束后环境变量自动恢复原值。

**为什么在本课使用：** 测试"环境变量覆盖配置文件"这个核心功能时，需要设置 `ULTRABOT_` 前缀的环境变量。`monkeypatch.setenv()` 确保测试隔离——不会影响其他测试或真实的系统环境。
