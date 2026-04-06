# ultrabot/cli/commands.py  （结构概览 — 在课程 8、17、19 中构建）
"""ultrabot 助手框架的 CLI 命令。"""

from typing import Annotated, Optional

import typer
from ultrabot import __version__

app = typer.Typer(
    name="ultrabot",
    help="ultrabot -- A robust personal AI assistant framework.",
    add_completion=False,
    no_args_is_help=True,
)

# ── 注册在 app 上的命令 ──────────────────────────────
# @app.command() onboard     — 初始化配置 + 工作空间
# @app.command() agent       — 交互式聊天或单次消息
# @app.command() gateway     — 启动所有消息通道
# @app.command() webui       — 启动 Web 仪表盘
# @app.command() status      — 显示提供商/通道状态
# experts 子命令组：
#   experts list              — 列出已加载的专家人设
#   experts info <slug>       — 显示专家详情
#   experts search <query>    — 按关键字搜索
#   experts sync              — 从 GitHub 下载


def version_callback(value: bool) -> None:
    if value:
        typer.echo(f"ultrabot {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[Optional[bool],
        typer.Option("--version", "-V", callback=version_callback, is_eager=True),
    ] = None,
) -> None:
    """ultrabot -- personal AI assistant framework."""
