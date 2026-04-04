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
