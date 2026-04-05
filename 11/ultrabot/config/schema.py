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
    models: list[str] = Field(default_factory=list, description="Model IDs this provider serves.")


class ProvidersConfig(Base):
    """所有提供者插槽。

    内置提供者为显式字段；任意自定义提供者通过 extra="allow" 支持，
    只需在 config.json 中添加新的键即可，无需修改代码。

    取自 ultrabot/config/schema.py 第 74-89 行。
    """
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="allow",
    )

    # 内置提供者
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

    def all_providers(self) -> dict[str, ProviderConfig]:
        """返回所有提供者（内置字段 + 自定义 extra）的名称→配置映射。"""
        result: dict[str, ProviderConfig] = {}
        # 内置字段
        for name in type(self).model_fields:
            result[name] = getattr(self, name)
        # 自定义提供者（通过 extra="allow" 接受的任意键）
        for name, val in (self.__pydantic_extra__ or {}).items():
            if isinstance(val, ProviderConfig):
                result[name] = val
            elif isinstance(val, dict):
                result[name] = ProviderConfig.model_validate(val)
        return result


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
    channels: dict = Field(default_factory=dict)

    def get_provider(self, model: str | None = None) -> str:
        """从模型字符串中解析出提供者名称。

        解析优先级：
        1. 精确匹配 — 检查每个提供者的 models 列表。
        2. 关键字匹配 — 使用注册表中的 keywords 进行模糊匹配。
        3. 默认值 — 返回 agents.defaults.provider。

        取自 ultrabot/config/schema.py 第 335-362 行。
        """
        if model is None:
            return self.agents.defaults.provider

        model_lower = model.lower()
        all_provs = self.providers.all_providers()

        # 1. 精确匹配 — 检查每个提供者配置中声明的 models 列表
        for name, prov in all_provs.items():
            if prov.enabled and model in prov.models:
                return name

        # 2. 关键字匹配 — 使用注册表中的 keywords 进行模糊匹配
        from ultrabot.providers.registry import PROVIDERS as _SPECS
        for spec in _SPECS:
            prov_cfg = all_provs.get(spec.name)
            if prov_cfg and prov_cfg.enabled:
                for kw in spec.keywords:
                    if kw in model_lower:
                        return spec.name

        return self.agents.defaults.provider

    def get_api_key(self, provider: str | None = None) -> str | None:
        """返回指定提供者的 API 密钥。"""
        name = provider or self.agents.defaults.provider
        prov = self.providers.all_providers().get(name)
        return prov.api_key if prov else None
