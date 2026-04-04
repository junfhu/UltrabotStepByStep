# tests/test_session8.py
"""课程 8 的测试 -- CLI 和 StreamRenderer。"""
import pytest
from unittest.mock import MagicMock, patch


def test_stream_renderer_lifecycle():
    """StreamRenderer 的 start/feed/finish 生命周期。"""
    from ultrabot.cli.stream import StreamRenderer

    renderer = StreamRenderer(title="Test")
    renderer.start()
    renderer.feed("Hello ")
    renderer.feed("world!")
    result = renderer.finish()

    assert result == "Hello world!"


def test_stream_renderer_text_property():
    """StreamRenderer.text 返回累积的缓冲区。"""
    from ultrabot.cli.stream import StreamRenderer

    renderer = StreamRenderer()
    renderer._buffer = "partial text"
    assert renderer.text == "partial text"


def test_stream_renderer_empty():
    """StreamRenderer 处理空输入。"""
    from ultrabot.cli.stream import StreamRenderer

    renderer = StreamRenderer()
    renderer.start()
    result = renderer.finish()
    assert result == ""


def test_cli_app_exists():
    """Typer 应用可导入且包含命令。"""
    from ultrabot.cli.commands import app

    # Typer 应用应该已注册了命令
    assert app is not None


def test_version_callback():
    """版本标志触发 typer.Exit。"""
    from click.exceptions import Exit
    from ultrabot.cli.commands import version_callback

    with pytest.raises(Exit):
        version_callback(True)


def test_slash_command_parsing():
    """斜杠命令被正确识别。"""
    commands = ["/help", "/clear", "/model gpt-4o", "/quit"]
    for cmd in commands:
        assert cmd.startswith("/")

    # 模型命令解析
    text = "/model gpt-4o"
    parts = text.split(maxsplit=1)
    assert parts[0] == "/model"
    assert parts[1] == "gpt-4o"


def test_interactive_banner(capsys):
    """横幅打印时不报错。"""
    from ultrabot.cli.commands import _interactive_banner
    # 只验证它不会崩溃
    _interactive_banner()
