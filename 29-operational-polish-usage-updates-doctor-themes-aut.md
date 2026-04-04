# Ultrabot：30 课程开发指南
**从零开始构建一个生产级 AI 助手框架。**
本指南将带你从"向 LLM 问好"一步步走到一个完整的多提供者、多通道 AI 智能体，具备工具调用、记忆、安全防护和 Web 界面。每节课程都建立在上一节课的基础之上。每节课都包含可运行的代码和测试。  
本教程的主要思路来自于
- Nanobot (https://github.com/HKUDS/nanobot)
- Learn-Claude-Code (https://github.com/shareAI-lab/learn-claude-code/)

本课程设计由AI辅助下完成，因为课程自身也在不停修正，请参考 https://github.com/junfhu/UltrabotStepByStep，如果您觉得对您有帮助，请帮助点亮一颗星。  
本课程中使用的大模型提供商是火山引擎Code Plan，如果正好你也需要，可以使用我的邀请码获取9折优惠 https://volcengine.com/L/_01BJCkKdMc/  邀请码：HHCDB4J4）  



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

## 本课使用的 Python 知识

### `from __future__ import annotations`（延迟注解求值）

这是一个特殊的导入语句，让 Python 将类型注解保存为字符串而不立即求值。这样可以使用 `list[str]`、`dict[str, float]` 等现代泛型语法，而不需要担心 Python 版本兼容性问题。

```python
from __future__ import annotations

def get_summary() -> dict[str, Any]:  # 不用 Dict[str, Any]
    ...
```

**为什么在本课中使用：** 本课涉及大量数据类和函数，类型注解中广泛使用了 `list[str]`、`dict[str, dict[str, float]]` 等现代语法，延迟求值确保代码在各版本 Python 中兼容。

### `collections.defaultdict`（默认字典）

`defaultdict` 是 `dict` 的一个子类，当访问不存在的键时，会自动用指定的工厂函数创建默认值，而不是抛出 `KeyError`。

```python
from collections import defaultdict

# 普通字典会报 KeyError
word_count = defaultdict(int)       # 默认值为 0
word_count["hello"] += 1            # 自动创建键 "hello"，值为 0，然后 +1

# 默认值为空列表
groups = defaultdict(list)
groups["team_a"].append("Alice")    # 自动创建空列表再追加
```

**为什么在本课中使用：** `UsageTracker` 中 `self._by_model` 和 `self._daily` 使用 `defaultdict(lambda: {"tokens": 0, "cost": 0.0})`，这样在首次记录某个模型或日期的用量时，不需要先检查键是否存在再初始化，代码更简洁。

### `@dataclass` 与 `field(default_factory=...)`

`@dataclass` 自动生成 `__init__` 等方法。`field(default_factory=...)` 用于设置可变类型（列表、字典）的默认值，避免所有实例共享同一个可变对象的经典陷阱。

```python
from dataclasses import dataclass, field

@dataclass
class UsageRecord:
    timestamp: float = field(default_factory=time.time)  # 每次创建时取当前时间
    tool_calls: list[str] = field(default_factory=list)  # 每个实例独立的列表
```

**为什么在本课中使用：** `UsageRecord` 有多个字段需要可变默认值：`timestamp` 默认为当前时间戳、`tool_calls` 默认为空列表。`field(default_factory=...)` 确保每个记录实例都有自己独立的值。

### `__dataclass_fields__`（数据类内省）

数据类自动生成的 `__dataclass_fields__` 属性是一个字典，包含所有字段的名称和元数据。可以用它来实现通用的序列化/反序列化逻辑。

```python
@dataclass
class Point:
    x: float = 0.0
    y: float = 0.0

print(Point.__dataclass_fields__.keys())  # dict_keys(['x', 'y'])
```

**为什么在本课中使用：** `UsageRecord.to_dict()` 用 `self.__dataclass_fields__` 遍历所有字段名来生成字典，`from_dict()` 用它来过滤无效的键。这样新增字段时不需要手动更新序列化代码。

### `@classmethod`（类方法）与工厂模式

`@classmethod` 定义一个以类（`cls`）而不是实例（`self`）为第一个参数的方法。常用于创建"工厂方法"——提供额外的方式来创建类的实例。

```python
class User:
    def __init__(self, name, age):
        self.name = name
        self.age = age

    @classmethod
    def from_dict(cls, data):
        return cls(name=data["name"], age=data["age"])

user = User.from_dict({"name": "Alice", "age": 30})
```

**为什么在本课中使用：** `UsageRecord.from_dict()` 是一个类方法工厂，从字典创建 `UsageRecord` 实例。这在从 JSON 文件或数据库加载用量记录时非常方便。

### 装饰器工厂（Decorator Factory）

装饰器工厂是一个返回装饰器的函数。通过外层函数接收参数，内层函数接收被装饰的函数。

```python
def repeat(n):
    """装饰器工厂 — 接收参数并返回装饰器"""
    def decorator(fn):
        def wrapper(*args, **kwargs):
            for _ in range(n):
                fn(*args, **kwargs)
        return wrapper
    return decorator

@repeat(3)
def greet():
    print("Hello!")
```

**为什么在本课中使用：** `register_migration(version, name, description)` 是一个装饰器工厂，接收迁移的版本号、名称等参数，返回一个装饰器。用 `@register_migration(1, "add-config-version")` 语法可以同时定义迁移函数和注册它的元数据。

### `lambda` 表达式

`lambda` 是定义匿名函数的简洁方式，适合只需要一个简单表达式的场景。

```python
# 普通函数
def double(x):
    return x * 2

# 等价的 lambda
double = lambda x: x * 2

# 常用于 sort、filter 等
items.sort(key=lambda m: m.version)
```

**为什么在本课中使用：** `defaultdict(lambda: {"tokens": 0, "cost": 0.0})` 用 lambda 定义了一个每次被调用都返回新字典的工厂函数。`_MIGRATIONS.sort(key=lambda m: m.version)` 用 lambda 指定按版本号排序。

### `datetime.date`（日期处理）

`date` 类表示一个日期（年-月-日），`date.today()` 返回当前日期，`.isoformat()` 返回 ISO 格式字符串。

```python
from datetime import date

today = date.today()
print(today.isoformat())  # "2025-01-15"
```

**为什么在本课中使用：** `UsageTracker.record()` 用 `date.today().isoformat()` 获取当前日期作为键，按天汇总用量数据。ISO 格式的日期字符串既人类可读又方便排序。

### `pathlib.Path`（现代路径处理）

`pathlib.Path` 是 Python 3 引入的面向对象路径处理方式，比 `os.path` 更直观，支持 `/` 运算符拼接路径。

```python
from pathlib import Path

config_dir = Path.home() / ".ultrabot"
config_file = config_dir / "config.json"
config_file.write_text('{"version": 1}')

if config_file.exists():
    content = config_file.read_text()
```

**为什么在本课中使用：** 用量追踪器、配置诊断、主题管理器都需要操作文件路径。`Path` 让路径拼接（`data_dir / "usage.json"`）和文件操作（`.exists()`、`.read_text()`、`.write_text()`）更简洁自然。

### `time.time()`（Unix 时间戳）

`time.time()` 返回当前时间的 Unix 时间戳（自 1970 年 1 月 1 日以来的秒数，浮点数）。

```python
import time

start = time.time()
print(start)  # 1705312345.123456
```

**为什么在本课中使用：** `UsageRecord` 的 `timestamp` 字段默认值为 `time.time()`，记录每次 API 调用的精确时间。时间戳是通用的时间表示方式，方便存储和比较。

### 嵌套字典类型注解

Python 类型注解支持嵌套使用，精确描述多层嵌套的数据结构。

```python
# 提供商 → 模型 → 价格类型 → 价格
PRICING: dict[str, dict[str, dict[str, float]]] = {
    "anthropic": {
        "claude-sonnet-4-20250514": {"input": 3.0, "output": 15.0},
    }
}
```

**为什么在本课中使用：** `PRICING` 定价表是三层嵌套字典（提供商 → 模型名 → 价格类型 → 价格值），精确的类型注解 `dict[str, dict[str, dict[str, float]]]` 让数据结构一目了然。

### `while` 循环与 FIFO 淘汰策略

`while` 循环在条件为真时重复执行，常用于需要动态判断终止条件的场景。FIFO（先进先出）淘汰是一种简单的缓存策略。

```python
# 当记录数超过上限时，移除最旧的记录
while len(records) > max_records:
    records.pop(0)  # 移除第一个（最旧的）
```

**为什么在本课中使用：** `UsageTracker.record()` 在每次添加新记录后检查列表长度，用 `while` 循环和 `pop(0)` 确保记录数不超过 `max_records`（默认 10000），防止内存无限增长。

### 轮询算法（Round-Robin）

轮询是一种简单的负载均衡策略，依次循环使用每个可用的资源。常用取模运算 `%` 实现循环。

```python
class RoundRobin:
    def __init__(self, items):
        self._items = items
        self._index = 0

    def next(self):
        item = self._items[self._index]
        self._index = (self._index + 1) % len(self._items)  # 取模实现循环
        return item
```

**为什么在本课中使用：** `AuthRotator.get_next_key()` 用轮询算法在多个 API 密钥之间循环切换，`self._current_index = (self._current_index + 1) % len(self._profiles)` 确保用完最后一个密钥后自动回到第一个。

### `pytest` 中的 `tmp_path` 固件

`tmp_path` 是 pytest 内置的固件（fixture），自动提供一个临时目录的 `Path` 对象，测试结束后自动清理。

```python
def test_save_file(tmp_path):
    file = tmp_path / "test.txt"
    file.write_text("hello")
    assert file.read_text() == "hello"
    # 测试结束后 tmp_path 目录自动删除
```

**为什么在本课中使用：** `TestConfigDoctor` 和 `TestPairing` 需要创建临时的配置文件或数据目录。`tmp_path` 提供隔离的临时目录，确保测试不会互相干扰，也不会在磁盘上留下垃圾文件。

### `unittest.mock.MagicMock`（模拟对象）

`MagicMock` 可以模拟任何对象，对它的任何属性访问和方法调用都会返回新的 `MagicMock`。常用于测试中替代外部依赖。

```python
from unittest.mock import MagicMock

api = MagicMock()
api.get_user.return_value = {"name": "Alice"}

result = api.get_user(123)
print(result)  # {"name": "Alice"}
api.get_user.assert_called_once_with(123)
```

**为什么在本课中使用：** 测试运维功能时，不需要连接真正的 LLM API 或外部服务。`MagicMock` 可以模拟配置对象、API 客户端等依赖，让测试快速、独立地运行。

### 枚举模式（Enum-like Pattern）

枚举用于定义一组命名常量，限制变量只能取预定义的值。

```python
from enum import Enum

class CredentialState(Enum):
    ACTIVE = "active"
    COOLDOWN = "cooldown"
    FAILED = "failed"

state = CredentialState.ACTIVE
if state == CredentialState.COOLDOWN:
    print("密钥在冷却中")
```

**为什么在本课中使用：** `CredentialState`（ACTIVE/COOLDOWN/FAILED）、`PairingPolicy`（OPEN/PAIRING/CLOSED）、`ActivationMode`（mention/always）等都使用枚举来表示有限的状态集。枚举让代码比使用裸字符串更安全，IDE 可以提供自动补全。

### `defaultdict` 与 `lambda` 结合

`defaultdict` 的参数是一个无参工厂函数，`lambda` 可以方便地定义返回复杂默认值的工厂函数。

```python
from collections import defaultdict

# 每个键的默认值是一个包含初始计数器的字典
stats = defaultdict(lambda: {"count": 0, "total": 0.0})
stats["model_a"]["count"] += 1
stats["model_a"]["total"] += 0.5
# stats["model_b"] 自动创建为 {"count": 0, "total": 0.0}
```

**为什么在本课中使用：** `self._daily` 使用 `defaultdict(lambda: {"tokens": 0, "cost": 0.0, "calls": 0})`，每天第一次记录数据时自动创建带有三个计数器的字典，无需手动检查和初始化。

### `json` 模块（JSON 序列化）

`json` 模块用于在 Python 对象和 JSON 字符串之间转换。`json.dumps()` 将对象转为 JSON 字符串，`json.loads()` 将 JSON 字符串转为对象。

```python
import json

data = {"name": "Alice", "scores": [95, 87, 92]}
json_str = json.dumps(data, indent=2)  # 转为格式化的 JSON 字符串
parsed = json.loads(json_str)           # 从 JSON 字符串转回字典
```

**为什么在本课中使用：** 配置文件以 JSON 格式存储，配置诊断和迁移系统需要用 `json` 模块读取、修改和写回配置。用量记录的持久化也使用 JSON 格式。

### `list.sort(key=...)` 自定义排序

`list.sort()` 方法接受一个 `key` 参数，用于指定排序依据。`key` 是一个函数，返回用于比较的值。

```python
students = [{"name": "Alice", "grade": 85}, {"name": "Bob", "grade": 92}]
students.sort(key=lambda s: s["grade"])  # 按成绩排序
```

**为什么在本课中使用：** `_MIGRATIONS.sort(key=lambda m: m.version)` 确保迁移按版本号从小到大排序。迁移注册可能以任意顺序发生（通过装饰器），但执行时必须按版本号顺序应用。
