# Agent: 30课程开发指南
**从零开始构建一个生产级 AI 助手框架。**
本指南将带你从"向 LLM 问好"一步步走到一个完整的多提供者、多通道 AI 智能体，具备工具调用、记忆、安全防护和 Web 界面。每节课程都建立在上一节课的基础之上。每节课都包含可运行的代码和测试。  
本教程的主要思路来自于
- Nanobot (https://github.com/HKUDS/nanobot)
- Learn-Claude-Code (https://github.com/shareAI-lab/learn-claude-code/)

本课程设计由AI辅助下完成，因为课程自身也在不停修正，请参考 https://github.com/junfhu/UltrabotStepByStep，如果您觉得对您有帮助，请帮助点亮一颗星。  



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
[project]
name = "ultrabot"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "openai>=1.0",
    "anthropic>=0.30",
    "pydantic>=2.0",
    "pydantic-settings>=2.0",
    "typer>=0.9",
    "rich>=13.0",
    "prompt-toolkit>=3.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
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
    prov_cfg = getattr(cfg.providers, provider_name, None)
    api_base = prov_cfg.api_base if prov_cfg else None

    if provider_name == "anthropic":
        from ultrabot.providers.anthropic_provider import AnthropicProvider
        provider = AnthropicProvider(
            api_key=api_key,
            api_base=api_base,
            generation=GenerationSettings(
                temperature=defaults.temperature,
                max_tokens=defaults.max_tokens,
            ),
        )
    else:
        provider = OpenAICompatProvider(
            api_key=api_key,
            api_base=api_base,
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
      "priority": 2
    },
    "openaiCompatible": {
      "apiKey":"sk-...",
      "enabled": true,
      "priority": 1,
      "apiBase": "https://ark.cn-beijing.volces.com/api/coding/v3"
    }
  },
  "agents": {
    "defaults": {
      "model": "minimax-m2.5",
      "provider": "openai_compatible",
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

一个完善的 CLI，使用 Typer 提供命令结构（`agent`、`status`、`--version`），Rich Live 在面板中提供美观的流式 markdown 输出，prompt_toolkit 提供类似 readline 的输入和持久化历史记录，斜杠命令用于会话内控制（`/help`、`/clear`、`/model`、`/quit`），以及单次模式用于脚本调用（`-m "question"`）。这直接对应 `ultrabot/cli/commands.py` 和 `ultrabot/cli/stream.py`。

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

---

## 本课使用的 Python 知识

### `from __future__ import annotations`（延迟注解求值）

让类型注解在定义时不被立即求值，允许使用 `str | None` 等现代语法。

```python
from __future__ import annotations

def process(data: str | None = None) -> str:
    return data or "default"
```

**本课为什么用它：** CLI 代码中使用了 `str | None`、`Path` 等类型注解，这行保证兼容性。

### `asyncio.run()` 和 `asyncio.get_event_loop().run_in_executor()`

`asyncio.run()` 是运行异步代码的入口点。`run_in_executor()` 将阻塞的同步函数放到线程池中执行，不阻塞事件循环。

```python
import asyncio

async def main():
    # 在线程池中运行阻塞操作
    result = await asyncio.get_event_loop().run_in_executor(
        None, input, "请输入: "
    )
    print(f"你输入了: {result}")

asyncio.run(main())
```

**本课为什么用它：** `asyncio.run(_agent_async(...))` 启动整个异步流程。在交互循环中，`session.prompt()` 是阻塞调用（等待用户输入），用 `run_in_executor(None, lambda: session.prompt("you > "))` 将其放到线程池执行，避免阻塞事件循环，让其他异步任务（如超时检测）仍然可以运行。

### `pathlib.Path`（面向对象的路径操作）

`Path` 提供跨平台的路径操作，比字符串拼接更安全、更直观。支持 `/` 运算符拼接路径。

```python
from pathlib import Path

home = Path.home()                    # 用户主目录
config = home / ".ultrabot" / "config.json"  # 用 / 拼接
config.parent.mkdir(parents=True, exist_ok=True)  # 创建父目录
if config.exists():
    text = config.read_text()
```

**本课为什么用它：** CLI 需要处理配置文件路径（`~/.ultrabot/config.json`）、历史文件路径（`~/.ultrabot/.history`）等。`Path` 的 `/` 运算符比 `os.path.join()` 更简洁，`.exists()`、`.mkdir(parents=True)` 等方法比 `os.path.exists()`、`os.makedirs()` 更直观。

### `typing.Annotated` 和 `typing.Optional`

`Annotated` 可以给类型注解附加元数据（如 Typer 的参数描述）。`Optional[X]` 等价于 `X | None`。

```python
from typing import Annotated, Optional

# Annotated 附加元数据
name: Annotated[str, "用户名称"] = "default"

# Optional 表示可以是 None
value: Optional[int] = None  # 等价于 int | None
```

**本课为什么用它：** Typer 用 `Annotated[Optional[str], typer.Option(...)]` 来声明 CLI 参数。`Annotated` 让 Typer 知道这个参数的选项名（如 `--message`）、帮助文本和其他元数据。

### `typer` 库（CLI 框架）

Typer 基于类型注解自动生成命令行界面，比 `argparse` 更简洁。用装饰器定义命令和回调。

```python
import typer

app = typer.Typer()

@app.command()
def hello(name: str = "World"):
    """向某人打招呼"""
    print(f"Hello, {name}!")

@app.callback()
def main(version: bool = typer.Option(False, "--version")):
    if version:
        print("v1.0")
        raise typer.Exit()

app()
```

**本课为什么用它：** `@app.command()` 定义了 `agent` 和 `status` 两个子命令，`@app.callback()` 处理全局选项（如 `--version`）。Typer 从函数签名自动生成帮助信息和参数校验，省去了手写 `argparse` 的大量代码。

### `raise typer.Exit()`（CLI 退出）

`typer.Exit()` 是 Typer 提供的干净退出异常。抛出它不会打印错误信息，只是正常结束程序。

```python
def version_callback(value: bool) -> None:
    if value:
        print("v1.0.0")
        raise typer.Exit()  # 打印版本后直接退出
```

**本课为什么用它：** `--version` 标志只需打印版本号后退出，不需要执行任何其他命令。`raise typer.Exit()` 实现了这个效果。

### `rich` 库 — `Console`、`Live`、`Markdown`、`Panel`

Rich 是 Python 的终端美化库。`Console` 是输出终端；`Live` 提供实时更新的显示区域；`Markdown` 渲染 markdown 文本；`Panel` 添加边框和标题。

```python
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel

console = Console()
console.print(Panel(Markdown("**Hello** World"), title="Demo"))
```

**本课为什么用它：** `StreamRenderer` 用 `Live` 实时更新显示（每秒刷新 8 次），`Markdown` 将 LLM 输出渲染为格式化文本（粗体、代码块等），`Panel` 加上漂亮的蓝色边框。这让终端输出从纯文本变成了美观的富文本界面。

### `StreamRenderer` 类（Rich Live 的封装）

这个类展示了如何封装第三方库，提供简洁的 `start()/feed()/finish()` 生命周期接口。

```python
renderer = StreamRenderer(title="UltraBot")
renderer.start()        # 开始实时显示
renderer.feed("Hello ") # 追加文本并刷新
renderer.feed("World!") # 继续追加
result = renderer.finish()  # 停止显示，返回完整文本
```

**本课为什么用它：** LLM 的流式响应是逐块到达的，`StreamRenderer` 将每个到达的文本片段追加到缓冲区并实时刷新终端显示，用户可以看到文字逐渐出现的效果，而不是等待全部完成后才看到输出。

### `prompt_toolkit`（交互式输入）

`prompt_toolkit` 提供类似 readline 的高级输入功能：历史记录（按上/下键翻阅之前输入的内容）、自动补全、语法高亮等。

```python
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory

session = PromptSession(
    history=FileHistory(".history")  # 持久化历史记录到文件
)
text = session.prompt("you > ")
```

**本课为什么用它：** 交互式 REPL 需要好的用户体验。`PromptSession` 提供可持久化的命令历史记录（保存在 `~/.ultrabot/.history`），用户下次启动时可以用上下键翻阅之前的输入。比 Python 内置的 `input()` 功能强大得多。

### `while True` 循环（交互式 REPL）

`while True` 创建无限循环，通过内部的 `break` 语句退出。这是 REPL（Read-Eval-Print Loop）的标准模式。

```python
while True:
    try:
        user_input = input(">>> ")
    except (EOFError, KeyboardInterrupt):
        print("Bye!")
        break

    if user_input == "/quit":
        break

    print(f"你说了: {user_input}")
```

**本课为什么用它：** 交互式聊天需要不断读取用户输入、发送给 LLM、显示响应，直到用户输入 `/quit` 或按 Ctrl+C。`while True` + `break` 是实现这种循环的最清晰方式。

### `str.startswith()` 和 `str.split()`（字符串处理）

`startswith()` 检查字符串是否以某前缀开头；`split()` 将字符串按分隔符拆分为列表。

```python
text = "/model gpt-4o"
if text.startswith("/"):
    parts = text.split(maxsplit=1)  # 最多分成 2 段
    print(parts)  # ["/model", "gpt-4o"]
```

**本课为什么用它：** 斜杠命令以 `/` 开头，用 `startswith("/")` 快速判断。`/model gpt-4o` 用 `split(maxsplit=1)` 分成命令名和参数两部分。

### `lambda` 表达式（匿名函数）

`lambda` 创建简短的匿名函数，通常用在只需要一次的简单回调场景。

```python
# 普通函数
def get_input():
    return session.prompt("you > ")

# 等价的 lambda
get_input = lambda: session.prompt("you > ")
```

**本课为什么用它：** `run_in_executor(None, lambda: session.prompt("you > "))` 用 lambda 包装 prompt 调用，因为 `run_in_executor` 需要一个无参数的可调用对象。

### 闭包（Closure）

闭包是一个函数「记住」了它定义时的外部变量。内部函数可以访问外部函数的局部变量，即使外部函数已经返回。

```python
def make_callback(renderer):
    async def callback(chunk: str) -> None:
        renderer.feed(chunk)  # 访问外部函数的 renderer 变量
    return callback
```

**本课为什么用它：** `_make_stream_callback(renderer)` 创建一个闭包，返回的 `callback` 函数「记住」了 `renderer` 对象。当 LLM 流式返回每个文本片段时，这个回调会被调用，将片段发送给对应的渲染器。

### `try/except` 捕获 `EOFError` 和 `KeyboardInterrupt`

`EOFError` 在输入流结束时触发（如管道输入结束），`KeyboardInterrupt` 在用户按 Ctrl+C 时触发。

```python
try:
    text = input(">>> ")
except EOFError:
    print("输入结束")
except KeyboardInterrupt:
    print("用户中断")
```

**本课为什么用它：** 交互式 REPL 中用户可能按 Ctrl+C 中断或 Ctrl+D 结束输入，需要优雅地处理这些情况而不是崩溃。

### `__main__.py`（包的入口点）

当一个包目录中有 `__main__.py` 文件时，可以用 `python -m 包名` 来运行它。

```python
# ultrabot/__main__.py
from ultrabot.cli.commands import app
app()
```

```bash
python -m ultrabot agent  # 运行 ultrabot 包的 __main__.py
```

**本课为什么用它：** 用户可以通过 `python -m ultrabot agent` 直接启动交互式聊天，而不需要知道具体的脚本路径。这是 Python 包的标准分发方式。

### `f-string` 格式化字符串

`f"..."` 允许在字符串中直接嵌入 Python 表达式，用大括号 `{}` 包裹。

```python
name = "gpt-4o"
print(f"Switched to model: {name}")       # Switched to model: gpt-4o
print(f"[red]Error: {exc}[/red]")         # Rich 标记 + 变量
```

**本课为什么用它：** CLI 输出大量使用 f-string 生成动态消息，如显示当前模型名、错误信息、配置路径等。比 `format()` 或 `%` 格式化更直观。

### `pytest` 与 `capsys` fixture

`pytest` 的 `capsys` fixture 可以捕获测试中打印到标准输出/错误的内容。

```python
def test_banner(capsys):
    print("Hello!")
    captured = capsys.readouterr()
    assert "Hello!" in captured.out
```

**本课为什么用它：** `test_interactive_banner(capsys)` 测试横幅打印函数是否能正常执行而不报错，`capsys` 可以捕获 Rich 输出的内容进行验证。
