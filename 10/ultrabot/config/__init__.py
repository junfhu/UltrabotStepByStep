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
