# tests/test_session10.py
"""课程 10 测试：熔断器 + 提供者-模型映射。"""
import time

import pytest

from ultrabot.config.schema import Config, ProviderConfig
from ultrabot.providers.circuit_breaker import CircuitBreaker, CircuitState


# ── 熔断器 ──────────────────────────────────────────────────


def test_breaker_starts_closed():
    cb = CircuitBreaker(failure_threshold=3)
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute is True


def test_breaker_trips_after_threshold():
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=1.0)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.CLOSED   # 还没有
    cb.record_failure()
    assert cb.state == CircuitState.OPEN     # 已熔断！
    assert cb.can_execute is False


def test_breaker_recovers_after_timeout():
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1)
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    time.sleep(0.15)
    assert cb.state == CircuitState.HALF_OPEN
    assert cb.can_execute is True


def test_half_open_success_closes():
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.0)
    cb.record_failure()                      # CLOSED -> OPEN
    _ = cb.state                             # OPEN -> HALF_OPEN (timeout=0)
    cb.record_success()
    assert cb.state == CircuitState.CLOSED


def test_half_open_failure_reopens():
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.0)
    cb.record_failure()
    _ = cb.state                             # -> HALF_OPEN
    cb.record_failure()
    assert cb.state == CircuitState.OPEN


# ── 提供者-模型映射：精确匹配 ────────────────────────────────


def test_explicit_model_list_matches_first():
    """当模型在 ProviderConfig.models 中声明时，精确匹配优先。"""
    cfg = Config(
        providers={
            "openai_compatible": {"models": ["my-custom-model", "gpt-4o"], "priority": 1},
            "deepseek": {"models": ["deepseek-chat"], "priority": 2},
        }
    )
    assert cfg.get_provider("my-custom-model") == "openai_compatible"
    assert cfg.get_provider("deepseek-chat") == "deepseek"


def test_one_provider_multiple_models():
    """一个提供者可以服务多个模型。"""
    cfg = Config(
        providers={
            "openai_compatible": {
                "models": ["minimax-m2.5", "gpt-4o", "custom-finetune-v2"],
                "priority": 1,
            },
        }
    )
    for model in ["minimax-m2.5", "gpt-4o", "custom-finetune-v2"]:
        assert cfg.get_provider(model) == "openai_compatible"


# ── 提供者-模型映射：关键字回退 ──────────────────────────────


def test_keyword_fallback_when_no_explicit_models():
    """未在 models 列表中的模型，回退到注册表关键字匹配。"""
    cfg = Config()  # 默认配置，无显式 models
    assert cfg.get_provider("gpt-4o-mini") == "openai_compatible"
    assert cfg.get_provider("claude-3-opus") == "anthropic"
    assert cfg.get_provider("deepseek-chat") == "deepseek"


def test_explicit_overrides_keyword():
    """精确匹配优先于关键字匹配。"""
    cfg = Config(
        providers={
            "openai_compatible": {"models": ["deepseek-chat"], "priority": 1},
            "deepseek": {"enabled": True, "priority": 2},
        }
    )
    assert cfg.get_provider("deepseek-chat") == "openai_compatible"


# ── 提供者-模型映射：默认回退 ────────────────────────────────


def test_unknown_model_returns_default():
    """无法匹配的模型返回默认提供者。"""
    cfg = Config()
    assert cfg.get_provider("some-unknown-model-xyz") == cfg.agents.defaults.provider


def test_none_model_returns_default():
    """model=None 返回默认提供者。"""
    cfg = Config()
    assert cfg.get_provider(None) == cfg.agents.defaults.provider


# ── ProviderConfig.models 字段 ───────────────────────────────


def test_provider_config_models_default_empty():
    """ProviderConfig.models 默认为空列表。"""
    pc = ProviderConfig()
    assert pc.models == []


def test_provider_config_models_from_json():
    """ProviderConfig 可以从 JSON（camelCase）加载 models。"""
    pc = ProviderConfig.model_validate({"models": ["a", "b", "c"]})
    assert pc.models == ["a", "b", "c"]


# ── 自定义提供者（extra="allow"）────────────────────────────


def test_custom_provider_via_extra():
    """通过 config.json 添加自定义提供者，无需修改代码。"""
    cfg = Config(
        providers={
            "openrouter": {
                "api_key": "sk-or-test",
                "api_base": "https://openrouter.ai/api/v1",
                "models": ["openai/gpt-4o", "anthropic/claude-3-opus"],
                "priority": 10,
            },
        }
    )
    # 精确匹配自定义提供者
    assert cfg.get_provider("openai/gpt-4o") == "openrouter"
    assert cfg.get_provider("anthropic/claude-3-opus") == "openrouter"


def test_custom_provider_in_all_providers():
    """all_providers() 包含内置和自定义提供者。"""
    cfg = Config(
        providers={
            "bailian": {
                "api_key": "sk-bailian",
                "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "models": ["qwen-max"],
            },
            "my_private_llm": {
                "api_key": "secret",
                "api_base": "https://internal.example.com/v1",
                "models": ["my-finetune-v3"],
            },
        }
    )
    all_provs = cfg.providers.all_providers()
    # 内置提供者仍在
    assert "openai" in all_provs
    assert "anthropic" in all_provs
    # 自定义提供者也在
    assert "bailian" in all_provs
    assert "my_private_llm" in all_provs
    assert all_provs["bailian"].api_base == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert all_provs["my_private_llm"].models == ["my-finetune-v3"]


def test_custom_provider_keyword_fallback():
    """注册表中有 spec 的自定义提供者支持关键字匹配。"""
    cfg = Config(
        providers={
            "bailian": {
                "api_key": "sk-test",
                "enabled": True,
            },
        }
    )
    # "qwen" 是 bailian 的注册关键字
    assert cfg.get_provider("qwen-max") == "bailian"


def test_multiple_custom_providers_failover_order():
    """多个自定义提供者按 priority 排序。"""
    cfg = Config(
        providers={
            "openrouter": {
                "api_key": "sk-or",
                "api_base": "https://openrouter.ai/api/v1",
                "priority": 20,
                "models": ["gpt-4o"],
            },
            "volcengine": {
                "api_key": "sk-volc",
                "api_base": "https://ark.cn-beijing.volces.com/api/v3",
                "priority": 10,
                "models": ["gpt-4o"],
            },
        }
    )
    from ultrabot.providers.manager import ProviderManager
    mgr = ProviderManager(cfg)
    # volcengine (priority=10) 排在 openrouter (priority=20) 前面
    ordered = mgr._ordered_entries("gpt-4o")
    prio_names = [e.name for e in ordered if e.name in ("volcengine", "openrouter")]
    assert prio_names[0] == "volcengine"
    assert prio_names[1] == "openrouter"
