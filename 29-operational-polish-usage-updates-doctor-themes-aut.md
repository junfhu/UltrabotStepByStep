# 课程 29：运维完善 — 用量追踪、更新、配置诊断、主题、密钥轮换

**目标：** 添加使 ultrabot 达到生产就绪状态的剩余运维功能：用量追踪、自更新、配置诊断、主题、API 密钥轮换、群聊激活、设备配对、技能、MCP 和标题生成。

**你将学到：**
- 按模型的 token/成本追踪及定价表
- 自更新系统（基于 git 和 pip）
- 配置健康检查与自动修复
- 带迁移函数的模式版本控制
- 支持 YAML 自定义的 CLI 主题
- 带冷却时间的轮询式 API 密钥轮换
- 群聊激活模式和私聊配对
- 技能发现、MCP 客户端和标题生成（概述）

**新建文件：**
- `ultrabot/usage/tracker.py` — `UsageTracker`、`UsageRecord`、定价表
- `ultrabot/updater/update.py` — `UpdateChecker`、`check_update()`、`run_update()`
- `ultrabot/config/doctor.py` — `run_doctor()`、8 项健康检查
- `ultrabot/config/migrations.py` — `apply_migrations()`、迁移注册表
- `ultrabot/cli/themes.py` — `ThemeManager`、4 个内置主题
- `ultrabot/providers/auth_rotation.py` — `AuthRotator`、`AuthProfile`
- `ultrabot/channels/group_activation.py` — `check_activation()`、提及检测
- `ultrabot/channels/pairing.py` — `PairingManager`、审批码
- `ultrabot/skills/manager.py` — `SkillManager`、技能发现
- `ultrabot/mcp/client.py` — `MCPClient`、stdio/HTTP 传输
- `ultrabot/agent/title_generator.py` — `generate_title()`

### 步骤 1：用量追踪

追踪每次 API 调用的 token 使用量和成本。定价表覆盖主要提供商。

```python
# ultrabot/usage/tracker.py  （关键摘录 — 完整文件约 310 行）
"""LLM API 调用的用量和成本追踪。"""

from __future__ import annotations
import json, time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any
from loguru import logger

# ── 定价表（美元/百万 token） ──────────────────────────
PRICING: dict[str, dict[str, dict[str, float]]] = {
    "anthropic": {
        "claude-sonnet-4-20250514": {"input": 3.0, "output": 15.0,
                                       "cache_read": 0.3, "cache_write": 3.75},
        "claude-opus-4-20250514": {"input": 15.0, "output": 75.0,
                                     "cache_read": 1.5, "cache_write": 18.75},
        "claude-3-5-haiku-20241022": {"input": 0.8, "output": 4.0,
                                       "cache_read": 0.08, "cache_write": 1.0},
    },
    "openai": {
        "gpt-4o": {"input": 2.5, "output": 10.0},
        "gpt-4o-mini": {"input": 0.15, "output": 0.6},
    },
    "deepseek": {
        "deepseek-chat": {"input": 0.14, "output": 0.28, "cache_read": 0.014},
    },
}


@dataclass
class UsageRecord:
    """单次 API 调用的用量记录。"""
    timestamp: float = field(default_factory=time.time)
    provider: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    session_key: str = ""
    tool_calls: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, data: dict) -> UsageRecord:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def calculate_cost(provider: str, model: str, input_tokens: int = 0,
                   output_tokens: int = 0, **kwargs) -> float:
    """根据给定用量计算美元成本。"""
    provider_pricing = PRICING.get(provider, {})
    model_pricing = provider_pricing.get(model)
    if model_pricing is None:
        # 尝试前缀匹配
        for known, pricing in provider_pricing.items():
            if known in model.lower() or model.lower() in known:
                model_pricing = pricing
                break
    if model_pricing is None:
        return 0.0
    cost = input_tokens * model_pricing.get("input", 0) / 1_000_000
    cost += output_tokens * model_pricing.get("output", 0) / 1_000_000
    cost += kwargs.get("cache_read_tokens", 0) * model_pricing.get("cache_read", 0) / 1_000_000
    cost += kwargs.get("cache_write_tokens", 0) * model_pricing.get("cache_write", 0) / 1_000_000
    return cost


class UsageTracker:
    """追踪并持久化 LLM API 用量和成本。"""

    def __init__(self, data_dir: Path | None = None, max_records: int = 10000):
        self._data_dir = data_dir
        self._max_records = max_records
        self._records: list[UsageRecord] = []
        self._total_tokens = 0
        self._total_cost = 0.0
        self._by_model: dict[str, dict[str, float]] = defaultdict(
            lambda: {"tokens": 0, "cost": 0.0})
        self._daily: dict[str, dict[str, float]] = defaultdict(
            lambda: {"tokens": 0, "cost": 0.0, "calls": 0})

    def record(self, provider: str, model: str, raw_usage: dict,
               session_key: str = "", tool_names: list[str] | None = None) -> UsageRecord:
        """记录单次 API 调用的用量。"""
        cost = calculate_cost(provider, model,
                              raw_usage.get("input_tokens", 0),
                              raw_usage.get("output_tokens", 0))
        rec = UsageRecord(provider=provider, model=model, cost_usd=cost,
                          input_tokens=raw_usage.get("input_tokens", 0),
                          output_tokens=raw_usage.get("output_tokens", 0),
                          total_tokens=raw_usage.get("total_tokens", 0),
                          session_key=session_key, tool_calls=tool_names or [])
        self._records.append(rec)
        self._total_tokens += rec.total_tokens
        self._total_cost += rec.cost_usd
        today = date.today().isoformat()
        self._daily[today]["tokens"] += rec.total_tokens
        self._daily[today]["cost"] += rec.cost_usd
        self._daily[today]["calls"] += 1
        while len(self._records) > self._max_records:
            self._records.pop(0)
        return rec

    def get_summary(self) -> dict[str, Any]:
        return {"total_tokens": self._total_tokens,
                "total_cost_usd": round(self._total_cost, 6),
                "total_calls": len(self._records),
                "daily": dict(self._daily)}
```

### 步骤 2：配置迁移

版本化的迁移自动升级旧的配置格式。

```python
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
```

### 步骤 3：配置诊断

八项健康检查诊断常见问题。

```python
# ultrabot/config/doctor.py  （关键摘录）

def run_doctor(config_path: Path, data_dir: Path | None = None,
               repair: bool = False) -> DoctorReport:
    """运行所有健康检查并返回报告。"""
    report = DoctorReport()
    report.checks.append(check_config_file(config_path))    # 1. 合法 JSON？
    report.checks.append(check_config_version(config))       # 2. 需要迁移？
    report.checks.append(check_providers(config))            # 3. API 密钥已设置？
    report.checks.append(check_workspace(config))            # 4. 工作空间存在？
    report.checks.append(check_sessions_dir(data_dir))       # 5. 会话目录正常？
    report.warnings = check_security(config)                 # 6-8. 安全警告
    if repair:
        apply_migrations(config)  # 自动修复可修复的问题
    return report
```

### 步骤 4：主题管理器

四个内置主题加上 YAML 自定义主题。

```python
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
```

### 步骤 5：密钥轮换

带自动冷却的轮询式 API 密钥轮换。

```python
# ultrabot/providers/auth_rotation.py  （关键摘录）

class AuthProfile:
    """带有状态追踪的单个 API 凭证。
    
    ACTIVE → COOLDOWN（遇到速率限制时） → ACTIVE（冷却期过后）
    ACTIVE → FAILED（连续失败 3 次后）
    """
    key: str
    state: CredentialState = CredentialState.ACTIVE
    cooldown_until: float = 0.0
    consecutive_failures: int = 0

class AuthRotator:
    """跨多个 API 密钥的轮询式轮换。"""
    
    def get_next_key(self) -> str | None:
        """获取下一个可用密钥。所有密钥耗尽时返回 None。"""
        for _ in range(len(self._profiles)):
            profile = self._profiles[self._current_index]
            self._current_index = (self._current_index + 1) % len(self._profiles)
            if profile.is_available:
                return profile.key
        # 最后手段：重置失败的密钥
        for profile in self._profiles:
            if profile.state == CredentialState.FAILED:
                profile.reset()
                return profile.key
        return None

async def execute_with_rotation(rotator, execute, is_rate_limit=None):
    """使用自动密钥轮换执行异步函数，失败时自动切换。"""
    ...
```

### 步骤 6：群聊激活 + 配对（简述）

```python
# ultrabot/channels/group_activation.py
# 控制机器人在群聊中何时回复："mention" 模式（仅被 @ 时回复）
# 或 "always" 模式。check_activation() 是入口函数。

# ultrabot/channels/pairing.py
# PairingManager 为未知的私聊发送者生成审批码。
# 每个通道支持 OPEN、PAIRING 和 CLOSED 策略。
```

### 步骤 7：技能、MCP、标题生成（简述）

```python
# ultrabot/skills/manager.py
# SkillManager 从磁盘发现技能（SKILL.md + 可选 tools/）。
# 支持通过 reload() 方法热重载。

# ultrabot/mcp/client.py
# MCPClient 通过 stdio 或 HTTP 传输连接 MCP 服务器。
# 将每个服务器工具封装为本地 MCPToolWrapper(Tool)。

# ultrabot/agent/title_generator.py
# generate_title() 使用辅助客户端为对话创建 3-7 个词的标题。
# 失败时回退到第一条用户消息的前 50 个字符。
```

### 测试

```python
# tests/test_operational.py
"""运维功能的测试：用量、更新、配置诊断、主题、密钥轮换。"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock

from ultrabot.usage.tracker import UsageTracker, calculate_cost, UsageRecord
from ultrabot.config.doctor import (
    check_config_file, check_providers, DoctorReport, HealthCheck,
)
from ultrabot.config.migrations import (
    apply_migrations, get_config_version, needs_migration, CURRENT_VERSION,
)
from ultrabot.cli.themes import ThemeManager, Theme, ThemeColors
from ultrabot.providers.auth_rotation import AuthRotator, AuthProfile, CredentialState
from ultrabot.channels.group_activation import (
    check_activation, ActivationMode, set_bot_names,
)
from ultrabot.channels.pairing import PairingManager, PairingPolicy


class TestUsageTracker:
    def test_record_and_summary(self):
        tracker = UsageTracker()
        tracker.record("anthropic", "claude-sonnet-4-20250514",
                       {"input_tokens": 1000, "output_tokens": 500, "total_tokens": 1500})
        summary = tracker.get_summary()
        assert summary["total_tokens"] == 1500
        assert summary["total_cost_usd"] > 0

    def test_calculate_cost_known_model(self):
        cost = calculate_cost("anthropic", "claude-sonnet-4-20250514",
                              input_tokens=1000, output_tokens=500)
        # 1000 * 3.0/1M + 500 * 15.0/1M = 0.003 + 0.0075 = 0.0105
        assert abs(cost - 0.0105) < 0.001

    def test_calculate_cost_unknown_model(self):
        assert calculate_cost("unknown", "unknown-model", 1000, 500) == 0.0

    def test_fifo_eviction(self):
        tracker = UsageTracker(max_records=5)
        for i in range(10):
            tracker.record("openai", "gpt-4o",
                           {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150})
        assert tracker.get_summary()["total_calls"] == 5


class TestConfigMigrations:
    def test_needs_migration_fresh_config(self):
        config = {}
        assert needs_migration(config) is True

    def test_apply_all_migrations(self):
        config = {"openai_api_key": "sk-test123456789"}
        result = apply_migrations(config)
        assert result.to_version == CURRENT_VERSION
        assert len(result.applied) > 0

    def test_already_current(self):
        config = {"_configVersion": CURRENT_VERSION}
        result = apply_migrations(config)
        assert len(result.applied) == 0


class TestConfigDoctor:
    def test_check_config_file_missing(self, tmp_path):
        result = check_config_file(tmp_path / "nope.json")
        assert result.ok is False
        assert result.auto_fixable is True

    def test_check_config_file_valid(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text('{"providers": {}}')
        result = check_config_file(cfg)
        assert result.ok is True

    def test_check_providers_none_configured(self):
        result = check_providers({})
        assert result.ok is False

    def test_check_providers_configured(self):
        config = {"providers": {"anthropic": {"apiKey": "sk-test"}}}
        result = check_providers(config)
        assert result.ok is True


class TestThemeManager:
    def test_builtin_themes_loaded(self):
        mgr = ThemeManager()
        themes = mgr.list_themes()
        names = [t.name for t in themes]
        assert "default" in names
        assert "dark" in names
        assert "mono" in names

    def test_set_active(self):
        mgr = ThemeManager()
        assert mgr.set_active("dark") is True
        assert mgr.active.name == "dark"

    def test_set_unknown_theme_fails(self):
        mgr = ThemeManager()
        assert mgr.set_active("nonexistent") is False
        assert mgr.active.name == "default"  # 未改变


class TestAuthRotation:
    def test_single_key(self):
        rotator = AuthRotator(["key1"])
        assert rotator.get_next_key() == "key1"

    def test_round_robin(self):
        rotator = AuthRotator(["k1", "k2", "k3"])
        keys = [rotator.get_next_key() for _ in range(6)]
        assert keys == ["k1", "k2", "k3", "k1", "k2", "k3"]

    def test_cooldown_on_failure(self):
        rotator = AuthRotator(["k1", "k2"], cooldown_seconds=0.01)
        rotator.record_failure("k1")
        # k1 处于冷却中，所以下一个密钥应该是 k2
        assert rotator.get_next_key() == "k2"

    def test_dedup_keys(self):
        rotator = AuthRotator(["k1", "k1", "k2", ""])
        assert rotator.profile_count == 2

    def test_all_keys_exhausted(self):
        rotator = AuthRotator([])
        assert rotator.get_next_key() is None


class TestGroupActivation:
    def test_dm_always_responds(self):
        result = check_activation("hello", "session1", is_group=False)
        assert result.should_respond is True

    def test_group_mention_mode(self):
        set_bot_names(["ultrabot"])
        result = check_activation("hey there", "grp1", is_group=True)
        assert result.should_respond is False

        result = check_activation("@ultrabot help me", "grp1", is_group=True)
        assert result.should_respond is True


class TestPairing:
    def test_open_policy_approves_all(self, tmp_path):
        mgr = PairingManager(tmp_path, default_policy=PairingPolicy.OPEN)
        approved, code = mgr.check_sender("telegram", "user123")
        assert approved is True
        assert code is None

    def test_pairing_generates_code(self, tmp_path):
        mgr = PairingManager(tmp_path, default_policy=PairingPolicy.PAIRING)
        approved, code = mgr.check_sender("telegram", "user456")
        assert approved is False
        assert code is not None
        assert len(code) == 6

    def test_approve_by_code(self, tmp_path):
        mgr = PairingManager(tmp_path, default_policy=PairingPolicy.PAIRING)
        _, code = mgr.check_sender("telegram", "user789")
        request = mgr.approve_by_code(code)
        assert request is not None
        assert request.sender_id == "user789"
        # 现在已批准
        assert mgr.is_approved("telegram", "user789") is True
```

### 检查点

```bash
python -m pytest tests/test_operational.py -v
```

预期结果：所有测试通过，覆盖用量追踪、配置迁移、配置诊断检查、主题、密钥轮换、群聊激活和私聊配对。

### 本课成果

完整的运维层：带按模型定价的用量追踪、自更新（git + pip）、带 8 项健康检查和自动修复的配置诊断、模式迁移、4 个支持 YAML 自定义的 CLI 主题、轮询式 API 密钥轮换、群聊激活模式、带审批码的私聊配对、技能发现、MCP 客户端和标题生成。ultrabot 现已达到生产就绪状态。

---
