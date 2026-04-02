# 课程 21：守护进程管理器 + 心跳

**目标：** 将 ultrabot 作为系统守护进程运行（systemd/launchd），并对所有 LLM 提供者进行定期健康检查心跳。

**你将学到：**
- 支持 systemd（Linux）和 launchd（macOS）的 `DaemonManager`
- 服务文件生成（unit 文件和 plist）
- 安装、启动、停止、重启、状态查询和卸载的生命周期管理
- 可配置健康检查间隔的 `HeartbeatService`
- 提供者熔断器状态监控

**新建文件：**
- `ultrabot/daemon/__init__.py` — 包导出
- `ultrabot/daemon/manager.py` — 跨平台守护进程生命周期管理
- `ultrabot/heartbeat/__init__.py` — 包导出
- `ultrabot/heartbeat/service.py` — 定期提供者健康检查

### 步骤 1：DaemonStatus 和 DaemonInfo

```python
# ultrabot/daemon/manager.py
"""守护进程管理 -- 安装、启动、停止 ultrabot 作为系统服务。

支持 systemd（Linux）和 launchd（macOS）。
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from loguru import logger


class DaemonStatus(str, Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    NOT_INSTALLED = "not_installed"
    UNKNOWN = "unknown"


@dataclass
class DaemonInfo:
    """关于守护进程服务的信息。"""
    status: DaemonStatus
    pid: int | None = None
    service_file: str | None = None
    platform: str = ""


SERVICE_NAME = "ultrabot-gateway"
```

### 步骤 2：平台检测和服务文件生成

```python
def _get_platform() -> str:
    system = platform.system().lower()
    if system == "linux":
        return "linux"
    if system == "darwin":
        return "macos"
    return "unsupported"


def _systemd_unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / f"{SERVICE_NAME}.service"


def _launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / "com.ultrabot.gateway.plist"


def _get_ultrabot_command() -> str:
    which = shutil.which("ultrabot")
    if which:
        return which
    return f"{sys.executable} -m ultrabot"


def _generate_systemd_unit(env_vars: dict[str, str] | None = None) -> str:
    """生成 systemd 用户 unit 文件内容。"""
    cmd = _get_ultrabot_command()
    lines = [
        "[Unit]",
        "Description=Ultrabot Gateway",
        "After=network.target",
        "",
        "[Service]",
        "Type=simple",
        f"ExecStart={cmd} gateway",
        "Restart=on-failure",
        "RestartSec=5",
        f"WorkingDirectory={Path.home()}",
    ]
    if env_vars:
        for key, val in env_vars.items():
            lines.append(f"Environment={key}={val}")
    lines.extend(["", "[Install]", "WantedBy=default.target"])
    return "\n".join(lines)


def _generate_launchd_plist(env_vars: dict[str, str] | None = None) -> str:
    """生成 launchd plist 文件内容。"""
    cmd = _get_ultrabot_command()
    cmd_parts = cmd.split()
    program_args = "".join(
        f"    <string>{p}</string>\n" for p in cmd_parts + ["gateway"]
    )
    env_section = ""
    if env_vars:
        env_entries = "".join(
            f"      <key>{k}</key>\n      <string>{v}</string>\n"
            for k, v in env_vars.items()
        )
        env_section = (
            f"  <key>EnvironmentVariables</key>\n"
            f"  <dict>\n{env_entries}  </dict>"
        )
    log_dir = Path.home() / ".ultrabot" / "logs"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.ultrabot.gateway</string>
  <key>ProgramArguments</key>
  <array>
{program_args}  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>{log_dir}/gateway.out.log</string>
  <key>StandardErrorPath</key>
  <string>{log_dir}/gateway.err.log</string>
  <key>WorkingDirectory</key>
  <string>{Path.home()}</string>
{env_section}
</dict>
</plist>"""
```

### 步骤 3：生命周期函数

```python
def install(env_vars: dict[str, str] | None = None) -> DaemonInfo:
    """将 ultrabot gateway 安装为系统守护进程。"""
    plat = _get_platform()

    if plat == "linux":
        unit_path = _systemd_unit_path()
        unit_path.parent.mkdir(parents=True, exist_ok=True)
        unit_path.write_text(_generate_systemd_unit(env_vars))
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", SERVICE_NAME], check=True)
        logger.info("Systemd service installed: {}", unit_path)
        return DaemonInfo(status=DaemonStatus.STOPPED,
                          service_file=str(unit_path), platform=plat)

    elif plat == "macos":
        plist_path = _launchd_plist_path()
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        (Path.home() / ".ultrabot" / "logs").mkdir(parents=True, exist_ok=True)
        plist_path.write_text(_generate_launchd_plist(env_vars))
        logger.info("Launchd plist installed: {}", plist_path)
        return DaemonInfo(status=DaemonStatus.STOPPED,
                          service_file=str(plist_path), platform=plat)

    raise RuntimeError(f"Unsupported platform: {plat}")


def start() -> DaemonInfo:
    plat = _get_platform()
    if plat == "linux":
        subprocess.run(["systemctl", "--user", "start", SERVICE_NAME], check=True)
    elif plat == "macos":
        subprocess.run(["launchctl", "load", str(_launchd_plist_path())], check=True)
    else:
        raise RuntimeError(f"Unsupported platform: {plat}")
    return status()


def stop() -> DaemonInfo:
    plat = _get_platform()
    if plat == "linux":
        subprocess.run(["systemctl", "--user", "stop", SERVICE_NAME], check=True)
    elif plat == "macos":
        subprocess.run(["launchctl", "unload", str(_launchd_plist_path())], check=True)
    else:
        raise RuntimeError(f"Unsupported platform: {plat}")
    return status()


def restart() -> DaemonInfo:
    plat = _get_platform()
    if plat == "linux":
        subprocess.run(["systemctl", "--user", "restart", SERVICE_NAME], check=True)
    elif plat == "macos":
        stop()
        start()
    else:
        raise RuntimeError(f"Unsupported platform: {plat}")
    return status()


def uninstall() -> bool:
    plat = _get_platform()
    try:
        stop()
    except Exception:
        pass

    if plat == "linux":
        subprocess.run(["systemctl", "--user", "disable", SERVICE_NAME], check=False)
        unit_path = _systemd_unit_path()
        if unit_path.exists():
            unit_path.unlink()
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        return True
    elif plat == "macos":
        plist_path = _launchd_plist_path()
        if plist_path.exists():
            subprocess.run(["launchctl", "unload", str(plist_path)], check=False)
            plist_path.unlink()
        return True
    return False
```

### 步骤 4：状态查询

```python
def status() -> DaemonInfo:
    """获取当前守护进程状态及 PID 检测。"""
    plat = _get_platform()

    if plat == "linux":
        unit_path = _systemd_unit_path()
        if not unit_path.exists():
            return DaemonInfo(status=DaemonStatus.NOT_INSTALLED, platform=plat)
        try:
            result = subprocess.run(
                ["systemctl", "--user", "is-active", SERVICE_NAME],
                capture_output=True, text=True,
            )
            is_active = result.stdout.strip() == "active"
            pid = None
            if is_active:
                pid_result = subprocess.run(
                    ["systemctl", "--user", "show", SERVICE_NAME,
                     "--property=MainPID", "--value"],
                    capture_output=True, text=True,
                )
                try:
                    pid = int(pid_result.stdout.strip())
                except ValueError:
                    pass
            return DaemonInfo(
                status=DaemonStatus.RUNNING if is_active else DaemonStatus.STOPPED,
                pid=pid, service_file=str(unit_path), platform=plat,
            )
        except Exception:
            return DaemonInfo(status=DaemonStatus.UNKNOWN, platform=plat)

    elif plat == "macos":
        plist_path = _launchd_plist_path()
        if not plist_path.exists():
            return DaemonInfo(status=DaemonStatus.NOT_INSTALLED, platform=plat)
        try:
            result = subprocess.run(
                ["launchctl", "list", "com.ultrabot.gateway"],
                capture_output=True, text=True,
            )
            is_loaded = result.returncode == 0
            pid = None
            if is_loaded:
                for line in result.stdout.splitlines():
                    parts = line.strip().split("\t")
                    if len(parts) >= 1:
                        try:
                            pid = int(parts[0])
                        except ValueError:
                            pass
            return DaemonInfo(
                status=DaemonStatus.RUNNING if is_loaded else DaemonStatus.STOPPED,
                pid=pid, service_file=str(plist_path), platform=plat,
            )
        except Exception:
            return DaemonInfo(status=DaemonStatus.UNKNOWN, platform=plat)

    return DaemonInfo(status=DaemonStatus.NOT_INSTALLED, platform="unsupported")
```

### 步骤 5：HeartbeatService

心跳服务定期检查所有已配置的提供者，并记录其熔断器健康状态。

```python
# ultrabot/heartbeat/service.py
"""心跳服务 -- 对 LLM 提供者进行定期健康检查。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from ultrabot.providers.manager import ProviderManager


class HeartbeatService:
    """定期 ping 已配置的 LLM 提供者并记录其健康状态。

    参数：
        config: 心跳配置（间隔、是否启用）。可为 None。
        provider_manager: 用于访问每个提供者的 ProviderManager。
    """

    def __init__(
        self,
        config: Any | None,
        provider_manager: "ProviderManager",
    ) -> None:
        self._config = config
        self._provider_manager = provider_manager
        self._task: asyncio.Task[None] | None = None
        self._running = False

        # 使用合理的默认值提取设置
        if config is not None:
            self._enabled: bool = getattr(config, "enabled", True)
            self._interval: int = getattr(config, "interval_s", 30)
        else:
            self._enabled = False
            self._interval = 30

    async def start(self) -> None:
        if not self._enabled:
            logger.debug("Heartbeat service is disabled")
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="heartbeat")
        logger.info("Heartbeat service started (interval={}s)", self._interval)

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Heartbeat service stopped")

    async def _loop(self) -> None:
        """按配置的间隔运行 _check，直到停止。"""
        while self._running:
            try:
                await self._check()
            except Exception:
                logger.exception("Heartbeat check failed")
            await asyncio.sleep(self._interval)

    async def _check(self) -> None:
        """通过熔断器健康检查检测所有提供者并记录状态。"""
        health = self._provider_manager.health_check()
        for name, healthy in health.items():
            if healthy:
                logger.debug("Heartbeat: provider '{}' healthy (circuit closed)", name)
            else:
                logger.warning("Heartbeat: provider '{}' UNHEALTHY (circuit open)", name)
```

### 测试

```python
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
```

### 检查点

```bash
python -c "
from ultrabot.daemon.manager import (
    _generate_systemd_unit, _generate_launchd_plist,
    DaemonStatus, DaemonInfo, SERVICE_NAME,
)

print(f'Service name: {SERVICE_NAME}')
print()
print('=== systemd unit ===')
print(_generate_systemd_unit({'OPENAI_API_KEY': 'sk-***'}))
print()
print('=== DaemonInfo ===')
info = DaemonInfo(status=DaemonStatus.RUNNING, pid=42, platform='linux')
print(f'Status: {info.status}, PID: {info.pid}, Platform: {info.platform}')
"
```

预期输出：
```
Service name: ultrabot-gateway

=== systemd unit ===
[Unit]
Description=Ultrabot Gateway
After=network.target

[Service]
Type=simple
ExecStart=... gateway
Restart=on-failure
RestartSec=5
...
Environment=OPENAI_API_KEY=sk-***

[Install]
WantedBy=default.target

=== DaemonInfo ===
Status: running, PID: 42, Platform: linux
```

### 本课成果

一个跨平台守护进程管理器，为 ultrabot gateway 生成并管理 systemd（Linux）或
launchd（macOS）服务文件。结合定期检查提供者熔断器健康状态的 `HeartbeatService`，
ultrabot 可以作为后台服务可靠运行，支持自动重启和健康监控。

---
