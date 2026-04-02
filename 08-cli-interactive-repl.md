# 课程 8：CLI + 交互式 REPL

**目标：** 构建一个完善的命令行界面，支持流式输出、Rich 格式化和斜杠命令。

**你将学到：**
- 使用 Typer 组织 CLI 命令结构
- 使用 Rich Live 实现美观的流式输出
- 使用 prompt_toolkit 实现带历史记录的交互式 REPL
- 斜杠命令（`/help`、`/clear`、`/model`）
- StreamRenderer 实现渐进式 markdown 渲染

**新建文件：**
- `ultrabot/cli/commands.py` -- 带命令的 Typer 应用
- `ultrabot/cli/stream.py` -- 使用 Rich Live 的 StreamRenderer

### 步骤 1：安装 CLI 依赖

```bash
pip install typer rich prompt-toolkit
```

更新 `pyproject.toml`：

```toml
dependencies = [
    "openai>=1.0",
    "anthropic>=0.30",
    "pydantic>=2.0",
    "pydantic-settings>=2.0",
    "typer>=0.9",
    "rich>=13.0",
    "prompt-toolkit>=3.0",
]
```

### 步骤 2：构建 StreamRenderer

这让我们可以使用 Rich 的 Live 显示来实现美观的流式输出：

```python
# ultrabot/cli/stream.py
"""LLM 流式输出期间的渐进式终端输出流渲染器。

取自 ultrabot/cli/stream.py。
"""
from __future__ import annotations

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel


class StreamRenderer:
    """使用 Rich Live 渐进式渲染流式 LLM 输出。

    用法：
        renderer = StreamRenderer()
        renderer.start()
        for chunk in stream:
            renderer.feed(chunk)
        renderer.finish()

    取自 ultrabot/cli/stream.py 第 23-81 行。
    """

    def __init__(self, title: str = "UltraBot") -> None:
        self._console = Console()
        self._buffer: str = ""
        self._title = title
        self._live: Live | None = None

    def start(self) -> None:
        """开始 Rich Live 上下文以进行渐进式渲染。"""
        self._buffer = ""
        self._live = Live(
            self._render(),
            console=self._console,
            refresh_per_second=8,
            vertical_overflow="visible",
        )
        self._live.start()

    def feed(self, chunk: str) -> None:
        """追加一个文本片段并刷新显示。"""
        self._buffer += chunk
        if self._live is not None:
            self._live.update(self._render())

    def finish(self) -> str:
        """停止 Live 显示并返回完整文本。"""
        if self._live is not None:
            self._live.update(self._render())
            self._live.stop()
            self._live = None
        result = self._buffer
        self._buffer = ""
        return result

    def _render(self) -> Panel:
        """从当前缓冲区构建 Rich 可渲染对象。"""
        md = Markdown(self._buffer or "...")
        return Panel(md, title=self._title, border_style="blue")

    @property
    def text(self) -> str:
        """到目前为止累积的文本。"""
        return self._buffer
```

### 步骤 3：使用 Typer 构建 CLI

```python
# ultrabot/cli/commands.py
"""ultrabot 的 CLI 命令。

提供带有 agent（交互式聊天）和 status 命令的 Typer 应用。

取自 ultrabot/cli/commands.py。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

# ---------------------------------------------------------------------------
# Typer 应用（取自第 25-30 行）
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="ultrabot",
    help="UltraBot -- A personal AI assistant framework.",
    add_completion=False,
    no_args_is_help=True,
)

console = Console()
_DEFAULT_WORKSPACE = Path.home() / ".ultrabot"


def version_callback(value: bool) -> None:
    if value:
        console.print("ultrabot 0.1.0")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        Optional[bool],
        typer.Option("--version", "-V", callback=version_callback, is_eager=True),
    ] = None,
) -> None:
    """UltraBot -- personal AI assistant framework."""


# ---------------------------------------------------------------------------
# agent 命令（取自第 180-294 行）
# ---------------------------------------------------------------------------

@app.command()
def agent(
    message: Annotated[
        Optional[str],
        typer.Option("--message", "-m", help="One-shot message (skip interactive)."),
    ] = None,
    config: Annotated[
        Optional[Path],
        typer.Option("--config", "-c", help="Path to config file."),
    ] = None,
    model: Annotated[
        Optional[str],
        typer.Option("--model", help="Override the LLM model."),
    ] = None,
) -> None:
    """启动交互式聊天会话或发送单次消息。"""
    cfg_path = config or (_DEFAULT_WORKSPACE / "config.json")

    if not cfg_path.exists():
        console.print(
            f"[red]Config not found at {cfg_path}. "
            f"Run 'ultrabot onboard' first.[/red]"
        )
        raise typer.Exit(1)

    asyncio.run(_agent_async(cfg_path, message, model))


async def _agent_async(
    cfg_path: Path,
    message: str | None,
    model: str | None,
) -> None:
    """agent 命令的异步入口点。"""
    from ultrabot.config import load_config
    from ultrabot.providers.openai_compat import OpenAICompatProvider
    from ultrabot.providers.base import GenerationSettings
    from ultrabot.tools.base import ToolRegistry
    from ultrabot.tools.builtin import register_builtin_tools

    cfg = load_config(cfg_path)
    if model:
        cfg.agents.defaults.model = model

    defaults = cfg.agents.defaults

    # 从配置构建提供者
    provider_name = cfg.get_provider(defaults.model)
    api_key = cfg.get_api_key(provider_name)

    if provider_name == "anthropic":
        from ultrabot.providers.anthropic_provider import AnthropicProvider
        provider = AnthropicProvider(
            api_key=api_key,
            generation=GenerationSettings(
                temperature=defaults.temperature,
                max_tokens=defaults.max_tokens,
            ),
        )
    else:
        provider = OpenAICompatProvider(
            api_key=api_key,
            generation=GenerationSettings(
                temperature=defaults.temperature,
                max_tokens=defaults.max_tokens,
            ),
            default_model=defaults.model,
        )

    # 构建工具
    registry = ToolRegistry()
    register_builtin_tools(registry)

    if message:
        # 单次模式
        response = await provider.chat_stream_with_retry(
            messages=[
                {"role": "system", "content": "You are UltraBot, a helpful assistant."},
                {"role": "user", "content": message},
            ],
        )
        console.print(Markdown(response.content or ""))
        return

    # 交互模式
    _interactive_banner()
    await _interactive_loop(provider, registry, defaults.model)


def _interactive_banner() -> None:
    console.print(Panel(
        "UltraBot v0.1.0\n"
        "Type your message and press Enter.\n"
        "Commands: /help /clear /model <name> /quit",
        title="UltraBot",
        border_style="blue",
    ))


async def _interactive_loop(provider, registry, model: str) -> None:
    """带有 prompt_toolkit、Rich 流式输出和斜杠命令的交互式 REPL。

    取自 ultrabot/cli/commands.py 第 264-294 行。
    """
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    from ultrabot.cli.stream import StreamRenderer

    history_path = _DEFAULT_WORKSPACE / ".history"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    session: PromptSession[str] = PromptSession(
        history=FileHistory(str(history_path))
    )

    # 对话状态
    messages: list[dict] = [
        {"role": "system", "content": "You are UltraBot, a helpful assistant."},
    ]
    current_model = model

    while True:
        try:
            user_input = await asyncio.get_event_loop().run_in_executor(
                None, lambda: session.prompt("you > ")
            )
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Goodbye.[/dim]")
            break

        text = user_input.strip()
        if not text:
            continue

        # -- 斜杠命令 --
        if text.startswith("/"):
            if text in ("/quit", "/exit", "/q"):
                console.print("[dim]Goodbye.[/dim]")
                break

            elif text == "/help":
                console.print(Panel(
                    "/help    -- Show this help\n"
                    "/clear   -- Clear conversation history\n"
                    "/model X -- Switch to model X\n"
                    "/quit    -- Exit",
                    title="Commands",
                    border_style="cyan",
                ))
                continue

            elif text == "/clear":
                messages = [messages[0]]  # 保留系统提示词
                console.print("[dim]Conversation cleared.[/dim]")
                continue

            elif text.startswith("/model"):
                parts = text.split(maxsplit=1)
                if len(parts) > 1:
                    current_model = parts[1]
                    console.print(f"[dim]Switched to model: {current_model}[/dim]")
                else:
                    console.print(f"[dim]Current model: {current_model}[/dim]")
                continue

            else:
                console.print(f"[yellow]Unknown command: {text}[/yellow]")
                continue

        # -- 普通消息 --
        messages.append({"role": "user", "content": text})

        # 使用 Rich Live 渲染流式响应
        renderer = StreamRenderer(title="UltraBot")
        renderer.start()

        try:
            tool_defs = registry.get_definitions() or None
            response = await provider.chat_stream_with_retry(
                messages=messages,
                tools=tool_defs,
                model=current_model,
                on_content_delta=_make_stream_callback(renderer),
            )

            full_text = renderer.finish()

            # 将助手响应追加到历史记录
            messages.append({"role": "assistant", "content": response.content or full_text})

        except Exception as exc:
            renderer.finish()
            console.print(f"[red]Error: {exc}[/red]")


def _make_stream_callback(renderer):
    """创建一个将文本片段发送给渲染器的异步回调。"""
    async def callback(chunk: str) -> None:
        renderer.feed(chunk)
    return callback


# ---------------------------------------------------------------------------
# status 命令（取自第 386-432 行）
# ---------------------------------------------------------------------------

@app.command()
def status(
    config: Annotated[
        Optional[Path],
        typer.Option("--config", "-c", help="Path to config file."),
    ] = None,
) -> None:
    """显示提供者状态和配置信息。"""
    cfg_path = config or (_DEFAULT_WORKSPACE / "config.json")

    if not cfg_path.exists():
        console.print("[yellow]No config found. Run 'ultrabot onboard' first.[/yellow]")
        return

    from ultrabot.config import load_config

    cfg = load_config(cfg_path)
    defaults = cfg.agents.defaults

    console.print(Panel(
        f"Model:       {defaults.model}\n"
        f"Provider:    {defaults.provider}\n"
        f"Temperature: {defaults.temperature}\n"
        f"Max tokens:  {defaults.max_tokens}\n"
        f"Max iters:   {defaults.max_tool_iterations}",
        title="UltraBot Status",
        border_style="blue",
    ))
```

### 步骤 4：接入入口点

```python
# ultrabot/__main__.py
"""允许通过以下方式运行：python -m ultrabot"""
from ultrabot.cli.commands import app

app()
```

### 测试

```python
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
    """版本标志触发 SystemExit。"""
    from ultrabot.cli.commands import version_callback

    with pytest.raises(SystemExit):
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
```

### 检查点

首先，确保你有一个配置文件：

```bash
mkdir -p ~/.ultrabot
cat > ~/.ultrabot/config.json << 'EOF'
{
  "providers": {
    "openai": {
      "apiKey": "sk-...",
      "enabled": true,
      "priority": 1
    }
  },
  "agents": {
    "defaults": {
      "model": "gpt-4o-mini",
      "provider": "openai",
      "temperature": 0.7
    }
  }
}
EOF
```

然后运行交互式 REPL：

```bash
python -m ultrabot agent
```

预期输出：
```
╭─ UltraBot ──────────────────────────────────────────────╮
│ UltraBot v0.1.0                                         │
│ Type your message and press Enter.                       │
│ Commands: /help /clear /model <name> /quit               │
╰──────────────────────────────────────────────────────────╯

you > Write a haiku about coding

╭─ UltraBot ──────────────────────────────────────────────╮
│ Lines of logic flow,                                     │
│ Bugs hiding in the shadows,                              │
│ Tests bring peace of mind.                               │
╰──────────────────────────────────────────────────────────╯

you > /model gpt-4o
Switched to model: gpt-4o

you > /clear
Conversation cleared.

you > /quit
Goodbye.
```

响应在 Rich 面板中以 markdown 渲染方式实时流式输出。

单次模式也可以使用：

```bash
python -m ultrabot agent -m "What is the capital of France?"
```

### 本课成果

一个完善的 CLI，具备：
- **Typer** 提供命令结构（`agent`、`status`、`--version`）
- **Rich Live** 在面板中提供美观的流式 markdown 输出
- **prompt_toolkit** 提供类似 readline 的输入和持久化历史记录
- **斜杠命令** 用于会话内控制（`/help`、`/clear`、`/model`、`/quit`）
- **单次模式** 用于脚本调用（`-m "question"`）

这直接对应 `ultrabot/cli/commands.py` 和 `ultrabot/cli/stream.py`。

---

## 下一步

完成 8 节课程后，你已经拥有：

| 课程 | 你构建了什么 | 核心概念 |
|------|-------------|---------|
| 1 | `chat.py` | 消息列表、多轮对话 |
| 2 | `Agent` 类 | 流式输出、智能体循环 |
| 3 | 工具系统 | Tool ABC、ToolRegistry、工具调用 |
| 4 | 工具集 | 命名分组、组合 |
| 5 | 配置系统 | Pydantic、JSON、环境变量 |
| 6 | 提供者抽象 | LLMProvider ABC、重试逻辑 |
| 7 | Anthropic 提供者 | API 格式转换、适配器模式 |
| 8 | CLI + REPL | Typer、Rich、prompt_toolkit |

**第 2 部分（课程 9-16）即将推出：**
- 课程 9：会话 + 持久化
- 课程 10：安全守卫
- 课程 11：专家人设
- 课程 12：MCP 集成
- 课程 13：通道（Telegram、Discord、Slack）
- 课程 14：网关服务器
- 课程 15：记忆 + 上下文压缩
- 课程 16：定时任务 + 计划任务
# Ultrabot 开发者指南 — 第 2 部分（课程 9–16）

> **前置条件：** 你已完成课程 1–8。你的项目已经有了一个可工作的
> Agent，支持流式输出、工具调用、配置、提供者抽象
>（OpenAI 兼容 + Anthropic）和一个 CLI REPL。

---
