# ultrabot/webui/__init__.py
"""基于浏览器的 Web UI 聊天界面。"""

from ultrabot.webui.app import create_app, run_server

__all__ = ["create_app", "run_server"]
