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
