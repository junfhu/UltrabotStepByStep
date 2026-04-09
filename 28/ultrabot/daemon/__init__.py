# ultrabot/daemon/__init__.py
"""守护进程管理 -- 安装、启停和状态查询。"""

from ultrabot.daemon.manager import DaemonInfo, DaemonStatus

__all__ = ["DaemonInfo", "DaemonStatus"]
