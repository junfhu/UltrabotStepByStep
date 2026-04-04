# ultrabot/config/migrations.py
"""配置迁移系统 -- 版本化模式迁移。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from loguru import logger

CONFIG_VERSION_KEY = "_configVersion"
CURRENT_VERSION = 3


@dataclass
class Migration:
    """单次迁移的描述。"""
    version: int
    name: str
    description: str = ""
    migrate: Callable[[dict], tuple[dict, list[str]]] = lambda c: (c, [])


@dataclass
class MigrationResult:
    """迁移执行结果。"""
    from_version: int = 0
    to_version: int = 0
    applied: list[str] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)


# 迁移注册表
_MIGRATIONS: list[Migration] = []


def register_migration(version: int, name: str, description: str = ""):
    """注册迁移函数的装饰器。"""
    def decorator(fn):
        _MIGRATIONS.append(Migration(version=version, name=name,
                                      description=description, migrate=fn))
        _MIGRATIONS.sort(key=lambda m: m.version)
        return fn
    return decorator


def get_config_version(config: dict) -> int:
    """获取配置的当前版本号。"""
    return config.get(CONFIG_VERSION_KEY, 0)


def needs_migration(config: dict) -> bool:
    """检查配置是否需要迁移。"""
    return get_config_version(config) < CURRENT_VERSION


@register_migration(1, "add-config-version")
def _add_version(config: dict) -> tuple[dict, list[str]]:
    if CONFIG_VERSION_KEY not in config:
        config[CONFIG_VERSION_KEY] = 1
        return config, ["Added _configVersion field"]
    return config, []


@register_migration(2, "normalize-provider-keys")
def _normalize_providers(config: dict) -> tuple[dict, list[str]]:
    """将顶层 API 密钥（openai_api_key 等）移入 providers 部分，
    标准化 camelCase 与 snake_case。"""
    changes: list[str] = []
    providers = config.setdefault("providers", {})

    # 映射顶层 API 密钥到 providers
    key_mapping = {
        "openai_api_key": ("openai", "apiKey"),
        "anthropic_api_key": ("anthropic", "apiKey"),
        "deepseek_api_key": ("deepseek", "apiKey"),
    }
    for old_key, (provider_name, field_name) in key_mapping.items():
        if old_key in config:
            provider_config = providers.setdefault(provider_name, {})
            provider_config[field_name] = config.pop(old_key)
            changes.append(f"Moved {old_key} to providers.{provider_name}.{field_name}")

    config[CONFIG_VERSION_KEY] = 2
    return config, changes


@register_migration(3, "normalize-channel-config")
def _normalize_channels(config: dict) -> tuple[dict, list[str]]:
    """将顶层通道配置移入 channels 部分。"""
    changes: list[str] = []
    channels = config.setdefault("channels", {})

    channel_keys = {
        "telegram_token": ("telegram", "token"),
        "discord_token": ("discord", "token"),
        "slack_token": ("slack", "token"),
    }
    for old_key, (channel_name, field_name) in channel_keys.items():
        if old_key in config:
            channel_config = channels.setdefault(channel_name, {})
            channel_config[field_name] = config.pop(old_key)
            changes.append(f"Moved {old_key} to channels.{channel_name}.{field_name}")

    config[CONFIG_VERSION_KEY] = 3
    return config, changes


def apply_migrations(config: dict, target_version: int | None = None) -> MigrationResult:
    """对配置字典应用所有待执行的迁移。"""
    target = target_version if target_version is not None else CURRENT_VERSION
    from_ver = get_config_version(config)
    result = MigrationResult(from_version=from_ver, to_version=from_ver)

    for migration in _MIGRATIONS:
        current = get_config_version(config)
        if current >= target:
            break
        if migration.version <= current:
            continue
        logger.debug("Applying migration {}: {}", migration.version, migration.name)
        config, changes = migration.migrate(config)
        result.applied.append(migration.name)
        result.changes.extend(changes)
        result.to_version = migration.version

    # 确保最终版本号
    if result.to_version < from_ver:
        result.to_version = from_ver

    return result
