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
