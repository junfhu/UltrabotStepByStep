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
