# tests/test_daemon_heartbeat.py
"""守护进程管理器和心跳服务的测试。"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from ultrabot.daemon.manager import (
    DaemonStatus, DaemonInfo, _generate_systemd_unit, _generate_launchd_plist,
    _get_platform, SERVICE_NAME,
)
from ultrabot.heartbeat.service import HeartbeatService


class TestServiceFileGeneration:
    def test_systemd_unit(self):
        unit = _generate_systemd_unit()
        assert "[Unit]" in unit
        assert "[Service]" in unit
        assert "gateway" in unit
        assert "Restart=on-failure" in unit

    def test_systemd_unit_with_env(self):
        unit = _generate_systemd_unit(env_vars={"API_KEY": "test123"})
        assert "Environment=API_KEY=test123" in unit

    def test_launchd_plist(self):
        plist = _generate_launchd_plist()
        assert "com.ultrabot.gateway" in plist
        assert "<key>KeepAlive</key>" in plist
        assert "gateway" in plist

    def test_launchd_plist_with_env(self):
        plist = _generate_launchd_plist(env_vars={"MY_VAR": "value"})
        assert "<key>MY_VAR</key>" in plist
        assert "<string>value</string>" in plist


class TestDaemonInfo:
    def test_status_enum(self):
        info = DaemonInfo(status=DaemonStatus.RUNNING, pid=1234, platform="linux")
        assert info.status == "running"
        assert info.pid == 1234

    def test_not_installed(self):
        info = DaemonInfo(status=DaemonStatus.NOT_INSTALLED)
        assert info.status == "not_installed"
        assert info.pid is None


class TestHeartbeatService:
    @pytest.mark.asyncio
    async def test_disabled_by_default(self):
        pm = MagicMock()
        svc = HeartbeatService(config=None, provider_manager=pm)
        assert svc._enabled is False
        await svc.start()
        assert svc._task is None  # 禁用时不应启动

    @pytest.mark.asyncio
    async def test_enabled_with_config(self):
        config = MagicMock()
        config.enabled = True
        config.interval_s = 5
        pm = MagicMock()
        pm.health_check.return_value = {"openai": True, "anthropic": False}

        svc = HeartbeatService(config=config, provider_manager=pm)
        assert svc._enabled is True
        assert svc._interval == 5

    @pytest.mark.asyncio
    async def test_check_logs_health(self):
        config = MagicMock()
        config.enabled = True
        config.interval_s = 60
        pm = MagicMock()
        pm.health_check.return_value = {"openai": True, "local": False}

        svc = HeartbeatService(config=config, provider_manager=pm)
        await svc._check()
        pm.health_check.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_stop(self):
        config = MagicMock()
        config.enabled = True
        config.interval_s = 1
        pm = MagicMock()
        pm.health_check.return_value = {}

        svc = HeartbeatService(config=config, provider_manager=pm)
        await svc.start()
        assert svc._running is True
        assert svc._task is not None
        await svc.stop()
        assert svc._running is False
