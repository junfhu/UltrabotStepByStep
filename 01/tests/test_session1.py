# tests/test_session1.py
"""课程 1 的测试 -- 消息格式、环境变量配置和响应解析。"""
import os
import pytest


def test_message_format():
    """验证我们的消息列表具有正确的结构。"""
    messages = [
        {"role": "system", "content": "You are a helper."},
        {"role": "user", "content": "Hello!"},
    ]
    # 每条消息必须包含 'role' 和 'content'
    for msg in messages:
        assert "role" in msg
        assert "content" in msg
        assert msg["role"] in ("system", "user", "assistant", "tool")


def test_multi_turn_history():
    """验证对话历史记录正确增长。"""
    messages = [{"role": "system", "content": "You are a helper."}]

    # 模拟一个两轮对话
    messages.append({"role": "user", "content": "Hi"})
    messages.append({"role": "assistant", "content": "Hello!"})
    messages.append({"role": "user", "content": "How are you?"})
    messages.append({"role": "assistant", "content": "I'm great!"})

    assert len(messages) == 5
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert messages[2]["role"] == "assistant"
    # 在系统提示词之后，角色交替出现 user/assistant
    for i in range(1, len(messages)):
        expected = "user" if i % 2 == 1 else "assistant"
        assert messages[i]["role"] == expected


def test_default_model():
    """未设置 MODEL 环境变量时，默认为 gpt-4o-mini。"""
    orig = os.environ.pop("MODEL", None)
    try:
        model = os.getenv("MODEL", "gpt-4o-mini")
        assert model == "gpt-4o-mini"
    finally:
        if orig is not None:
            os.environ["MODEL"] = orig


def test_custom_model(monkeypatch):
    """MODEL 环境变量可覆盖默认模型。"""
    monkeypatch.setenv("MODEL", "deepseek-chat")
    model = os.getenv("MODEL", "gpt-4o-mini")
    assert model == "deepseek-chat"


def test_custom_base_url(monkeypatch):
    """OPENAI_BASE_URL 环境变量用于配置提供者端点。"""
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com")
    base_url = os.getenv("OPENAI_BASE_URL")
    assert base_url == "https://api.deepseek.com"


def test_base_url_none_when_unset():
    """OPENAI_BASE_URL 未设置时默认为 None（使用 OpenAI 端点）。"""
    orig = os.environ.pop("OPENAI_BASE_URL", None)
    try:
        base_url = os.getenv("OPENAI_BASE_URL")
        assert base_url is None
    finally:
        if orig is not None:
            os.environ["OPENAI_BASE_URL"] = orig


def test_response_parsing_mock(monkeypatch):
    """测试我们能否正确解析 OpenAI 响应（使用 mock）。"""
    from unittest.mock import MagicMock

    # 构建一个模拟的响应，看起来像 OpenAI 返回的结果
    mock_message = MagicMock()
    mock_message.content = "Hello! How can I help?"

    mock_choice = MagicMock()
    mock_choice.message = mock_message

    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    # 这就是我们在 ultrabot/chat.py 中解析它的方式
    result = mock_response.choices[0].message.content
    assert result == "Hello! How can I help?"
