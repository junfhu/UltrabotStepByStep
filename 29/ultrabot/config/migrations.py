# ultrabot/config/migrations.py  （关键摘录）
"""配置迁移系统 -- 版本化模式迁移。"""

CONFIG_VERSION_KEY = "_configVersion"
CURRENT_VERSION = 3

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

@register_migration(1, "add-config-version")
def _add_version(config: dict) -> tuple[dict, list[str]]:
    if CONFIG_VERSION_KEY not in config:
        config[CONFIG_VERSION_KEY] = 1
        return config, ["Added _configVersion field"]
    return config, []

@register_migration(2, "normalize-provider-keys")
def _normalize_providers(config: dict) -> tuple[dict, list[str]]:
    # 将顶层 API 密钥（openai_api_key）移入 providers 部分
    # 标准化 camelCase 与 snake_case
    ...

@register_migration(3, "normalize-channel-config")
def _normalize_channels(config: dict) -> tuple[dict, list[str]]:
    # 将顶层通道配置移入 channels 部分
    ...

def apply_migrations(config: dict, target_version: int | None = None) -> MigrationResult:
    """对配置字典应用所有待执行的迁移。"""
    ...
