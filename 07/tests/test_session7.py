# tests/test_session7.py
"""课程 7 的测试 -- Anthropic 提供者。"""
import json
import pytest
from ultrabot.providers.anthropic_provider import AnthropicProvider


def test_convert_messages_extracts_system():
    """系统消息被提取为单独的系统文本。"""
    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hello"},
    ]
    system_text, converted = AnthropicProvider._convert_messages(messages)

    assert system_text == "You are helpful."
    assert len(converted) == 1
    assert converted[0]["role"] == "user"


def test_convert_messages_tool_result():
    """OpenAI 工具结果变成 Anthropic tool_result 块。"""
    messages = [
        {"role": "user", "content": "List files"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "list_directory", "arguments": '{"path": "."}'}}
        ]},
        {"role": "tool", "tool_call_id": "call_1", "content": "file1.py\nfile2.py"},
    ]
    _, converted = AnthropicProvider._convert_messages(messages)

    # 工具结果应该是一个带有 tool_result 块的 user 消息
    tool_msg = converted[-1]
    assert tool_msg["role"] == "user"
    assert tool_msg["content"][0]["type"] == "tool_result"
    assert tool_msg["content"][0]["tool_use_id"] == "call_1"


def test_convert_tools_format():
    """OpenAI 工具定义被转换为 Anthropic 格式。"""
    openai_tools = [{
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    }]

    anthropic_tools = AnthropicProvider._convert_tools(openai_tools)
    assert len(anthropic_tools) == 1
    assert anthropic_tools[0]["name"] == "read_file"
    assert "input_schema" in anthropic_tools[0]
    assert "type" not in anthropic_tools[0]  # 没有 "type": "function"


def test_merge_consecutive_roles():
    """连续相同角色的消息被合并。"""
    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "user", "content": "World"},  # 连续的 user
    ]
    merged = AnthropicProvider._merge_consecutive_roles(messages)

    assert len(merged) == 1
    assert merged[0]["role"] == "user"
    # 内容应该被合并为块列表
    assert isinstance(merged[0]["content"], list)
    assert len(merged[0]["content"]) == 2


def test_map_stop_reason():
    """Anthropic 停止原因映射为 OpenAI 风格的原因。"""
    assert AnthropicProvider._map_stop_reason("end_turn") == "stop"
    assert AnthropicProvider._map_stop_reason("tool_use") == "tool_calls"
    assert AnthropicProvider._map_stop_reason("max_tokens") == "length"
    assert AnthropicProvider._map_stop_reason(None) is None


def test_assistant_message_with_tool_calls():
    """带有 tool_calls 的助手消息被转换为 tool_use 块。"""
    messages = [
        {"role": "assistant", "content": "Let me check.", "tool_calls": [
            {"id": "tc_1", "type": "function",
             "function": {"name": "read_file", "arguments": '{"path": "test.py"}'}},
        ]},
    ]
    _, converted = AnthropicProvider._convert_messages(messages)

    blocks = converted[0]["content"]
    assert blocks[0]["type"] == "text"
    assert blocks[0]["text"] == "Let me check."
    assert blocks[1]["type"] == "tool_use"
    assert blocks[1]["name"] == "read_file"
    assert blocks[1]["input"] == {"path": "test.py"}
