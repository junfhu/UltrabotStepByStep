# ultrabot/heartbeat/__init__.py
"""心跳服务 -- 定期上报运行状态。"""

from ultrabot.heartbeat.service import HeartbeatService

__all__ = ["HeartbeatService"]
