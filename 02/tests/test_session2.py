# tests/test_session2.py
"""课程 2 的测试 -- Agent 类和流式输出。"""
from unittest.mock import MagicMock

from ultrabot.agent import Agent, LLMResponse


def make_agent(model: str = "gpt-4o-mini", max_iterations: int = 10) -> Agent:
    """创建一个带 mock client 的 Agent，便于测试。"""
    client = MagicMock()
    return Agent(client=client, model=model, max_iterations=max_iterations)


def test_agent_init():
    """Agent 初始化时消息列表中包含系统提示词。"""
    agent = make_agent()

    assert len(agent._messages) == 1
    assert agent._messages[0]["role"] == "system"


def test_agent_appends_user_message():
    """Agent.run() 将用户消息和助手回复追加到历史记录。"""
    agent = make_agent()

    mock_response = LLMResponse(content="Hello!", tool_calls=[])
    agent._chat_stream = MagicMock(return_value=mock_response)

    result = agent.run("Hi there")

    assert result == "Hello!"
    assert len(agent._messages) == 3
    assert agent._messages[1] == {"role": "user", "content": "Hi there"}
    assert agent._messages[2] == {"role": "assistant", "content": "Hello!"}


def test_agent_max_iterations():
    """即使一直出现工具调用，Agent 也会在 max_iterations 后停止。"""
    agent = make_agent(max_iterations=2)

    response_with_tools = LLMResponse(
        content="",
        tool_calls=[{"id": "1", "function": {"name": "test", "arguments": "{}"}}],
    )
    agent._chat_stream = MagicMock(return_value=response_with_tools)

    result = agent.run("Do something")

    assert "maximum number of iterations" in result


def test_streaming_callback_is_forwarded():
    """run() 会把 on_content_delta 回调传给 _chat_stream。"""
    agent = make_agent()
    callback = MagicMock()

    mock_response = LLMResponse(content="Hello world", tool_calls=[])
    agent._chat_stream = MagicMock(return_value=mock_response)

    agent.run("Hi", on_content_delta=callback)

    agent._chat_stream.assert_called_once_with(callback)


def test_agent_clear():
    """Agent.clear() 重置为只包含系统提示词。"""
    agent = make_agent()
    mock_response = LLMResponse(content="Hi!", tool_calls=[])
    agent._chat_stream = MagicMock(return_value=mock_response)

    agent.run("Hello")
    assert len(agent._messages) == 3

    agent.clear()

    assert len(agent._messages) == 1
    assert agent._messages[0]["role"] == "system"
