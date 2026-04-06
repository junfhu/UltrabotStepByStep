# Ultrabot：30 课程开发指南
**从零开始构建一个生产级 AI 助手框架。**
本指南将带你从"向 LLM 问好"一步步走到一个完整的多提供者、多通道 AI 智能体，具备工具调用、记忆、安全防护和 Web 界面。每节课程都建立在上一节课的基础之上。每节课都包含可运行的代码和测试。  
本教程的主要思路来自于
- Nanobot (https://github.com/HKUDS/nanobot)
- Learn-Claude-Code (https://github.com/shareAI-lab/learn-claude-code/)

本课程设计由AI辅助下完成，因为课程自身也在不停修正，请参考 https://github.com/junfhu/UltrabotStepByStep，如果您觉得对您有帮助，请帮助点亮一颗星。  
本课程中使用的大模型提供商是火山引擎Code Plan，如果正好你也需要，可以使用我的邀请码获取9折优惠 https://volcengine.com/L/_01BJCkKdMc/  邀请码：HHCDB4J4）  



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

> **pytest 配置**：本课的异步测试使用 `@pytest.mark.asyncio`，需要在 `pyproject.toml` 中添加：
> ```toml
> [tool.pytest.ini_options]
> asyncio_mode = "auto"
> ```

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

## 本课使用的 Python 知识

### `from __future__ import annotations`

这是一个特殊的导入语句，让 Python 把所有类型注解当作字符串处理（延迟求值），而不是在定义时立即解析。这样做的好处是可以在类型注解中使用尚未定义的类名，也支持 `dict[str, str] | None` 这种新语法在较早版本的 Python 中使用。

```python
from __future__ import annotations

# 有了这个导入，下面的写法在 Python 3.9 也能用
def foo(data: dict[str, str] | None = None) -> None:
    pass
```

**为什么在本课中使用：** 本课代码大量使用了 `dict[str, str] | None`、`int | None` 等新式类型注解语法，加上这一行可以兼容 Python 3.9+。

### `Enum` 枚举类型与 `str, Enum` 多重继承

`Enum` 是 Python 内置的枚举类，用来定义一组命名的常量值。通过同时继承 `str` 和 `Enum`，枚举值可以直接当字符串使用。

```python
from enum import Enum

class Color(str, Enum):
    RED = "red"
    BLUE = "blue"

print(Color.RED == "red")  # True — 因为继承了 str
```

**为什么在本课中使用：** `DaemonStatus` 和 `ChunkMode` 继承 `str, Enum`，使得状态值既是枚举类型（防止拼写错误），又能直接与字符串比较（`info.status == "running"`）。

### `@dataclass` 数据类

`@dataclass` 装饰器可以自动为类生成 `__init__`、`__repr__`、`__eq__` 等方法，减少样板代码。只需声明字段和类型，Python 自动帮你写好构造函数。

```python
from dataclasses import dataclass

@dataclass
class Point:
    x: float
    y: float
    label: str = "origin"  # 带默认值的字段

p = Point(1.0, 2.0)
print(p)  # Point(x=1.0, y=2.0, label='origin')
```

**为什么在本课中使用：** `DaemonInfo` 用 `@dataclass` 定义，包含 `status`、`pid`、`service_file`、`platform` 四个字段，省去了手写 `__init__` 的麻烦。

### `pathlib.Path` 面向对象的路径操作

`pathlib.Path` 是 Python 3 推荐的文件路径处理方式，比 `os.path` 更直观。支持 `/` 运算符拼接路径，以及 `.exists()`、`.mkdir()`、`.write_text()` 等链式方法。

```python
from pathlib import Path

config_dir = Path.home() / ".config" / "myapp"
config_dir.mkdir(parents=True, exist_ok=True)  # 递归创建目录
(config_dir / "settings.txt").write_text("key=value")
```

**为什么在本课中使用：** 生成 systemd unit 文件和 launchd plist 文件时，需要拼接用户主目录下的路径并创建目录、写入内容，`Path` 让这些操作一气呵成。

### `subprocess.run()` 子进程调用

`subprocess.run()` 用于在 Python 中执行外部系统命令，比如调用 `systemctl` 或 `launchctl` 来管理系统服务。

```python
import subprocess

result = subprocess.run(
    ["systemctl", "--user", "is-active", "myservice"],
    capture_output=True,  # 捕获 stdout 和 stderr
    text=True,            # 输出为字符串而非 bytes
)
print(result.stdout.strip())  # "active" 或 "inactive"
```

**为什么在本课中使用：** 守护进程管理的核心就是调用系统命令（`systemctl`/`launchctl`）来安装、启动、停止和查询服务状态。

### `platform.system()` 平台检测

`platform.system()` 返回当前操作系统的名称字符串（如 `"Linux"`、`"Darwin"`、`"Windows"`），用于编写跨平台代码。

```python
import platform

system = platform.system().lower()
if system == "linux":
    print("在 Linux 上运行")
elif system == "darwin":
    print("在 macOS 上运行")
```

**为什么在本课中使用：** 守护进程管理器需要根据操作系统选择生成 systemd（Linux）还是 launchd（macOS）的服务文件。

### `shutil.which()` 查找可执行文件

`shutil.which()` 在系统 PATH 中查找指定命令的完整路径，类似于 shell 中的 `which` 命令。找不到时返回 `None`。

```python
import shutil

python_path = shutil.which("python3")
print(python_path)  # 例如 /usr/bin/python3
```

**为什么在本课中使用：** 用于检测 `ultrabot` 命令是否已安装在系统 PATH 中；如果找不到，就回退使用 `sys.executable -m ultrabot` 的方式运行。

### `async / await` 异步编程

`async def` 定义协程函数，`await` 用于等待异步操作完成。配合 `asyncio` 事件循环，可以在等待 I/O 时执行其他任务，实现非阻塞的并发。

```python
import asyncio

async def greet(name: str) -> str:
    await asyncio.sleep(1)  # 非阻塞等待 1 秒
    return f"Hello, {name}!"

result = asyncio.run(greet("World"))
```

**为什么在本课中使用：** `HeartbeatService` 需要在后台定期检查提供者健康状态。用异步编程可以在等待检查结果时不阻塞其他任务。

### `asyncio.create_task()` 创建异步任务

`asyncio.create_task()` 将一个协程包装为一个 `Task` 对象，放入事件循环中并发执行。任务可以被取消（`task.cancel()`）。

```python
import asyncio

async def background_job():
    while True:
        print("心跳...")
        await asyncio.sleep(5)

async def main():
    task = asyncio.create_task(background_job(), name="heartbeat")
    await asyncio.sleep(12)  # 让心跳跑几次
    task.cancel()            # 取消任务

asyncio.run(main())
```

**为什么在本课中使用：** `HeartbeatService.start()` 使用 `create_task()` 将心跳循环作为后台任务启动，`stop()` 时用 `cancel()` 优雅停止。

### `asyncio.CancelledError` 任务取消异常

当一个 `asyncio.Task` 被调用 `cancel()` 时，会在该任务内部抛出 `CancelledError`。捕获它可以执行清理操作。

```python
try:
    await some_task
except asyncio.CancelledError:
    print("任务被取消了，进行清理")
```

**为什么在本课中使用：** `HeartbeatService.stop()` 取消心跳任务后，需要 `await` 该任务并捕获 `CancelledError`，确保任务正确结束。

### `getattr()` 动态属性访问

`getattr(obj, name, default)` 可以按字符串名称获取对象的属性值，如果属性不存在则返回默认值，避免 `AttributeError`。

```python
class Config:
    timeout = 30

config = Config()
print(getattr(config, "timeout", 10))    # 30（属性存在）
print(getattr(config, "retries", 3))     # 3（属性不存在，用默认值）
```

**为什么在本课中使用：** `HeartbeatService` 接收的 `config` 对象结构可能不固定，用 `getattr(config, "enabled", True)` 安全地获取配置值，缺失时使用默认值。

### `TYPE_CHECKING` 条件导入

`typing.TYPE_CHECKING` 在运行时为 `False`，只有类型检查工具（如 mypy）运行时为 `True`。配合 `if TYPE_CHECKING:` 可以只在类型检查时导入某些模块，避免循环导入。

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from myapp.heavy_module import HeavyClass

def process(obj: "HeavyClass") -> None:  # 字符串形式的前向引用
    ...
```

**为什么在本课中使用：** `HeartbeatService` 需要引用 `ProviderManager` 的类型，但直接导入会导致循环依赖。`TYPE_CHECKING` 让运行时不实际导入，只供类型检查器使用。

### `try / except / raise` 异常处理

Python 的异常处理机制允许捕获运行时错误并做出响应。`raise` 用于主动抛出异常。

```python
try:
    result = int("abc")
except ValueError:
    print("转换失败")

# 主动抛出
if platform == "unsupported":
    raise RuntimeError(f"不支持的平台: {platform}")
```

**为什么在本课中使用：** 守护进程操作可能失败（如平台不支持、命令执行出错），需要用 `try/except` 优雅处理错误，用 `raise RuntimeError` 拒绝不支持的平台。

### `loguru` 第三方日志库

`loguru` 是一个比标准库 `logging` 更易用的日志框架，开箱即用，支持 `{}` 格式化占位符。

```python
from loguru import logger

logger.info("服务启动，端口={}", 8080)
logger.warning("连接失败: {}", "timeout")
logger.exception("发生异常")  # 自动附带堆栈信息
```

**为什么在本课中使用：** 守护进程和心跳服务需要记录关键事件（安装、启动、停止、健康检查结果），`loguru` 简洁的 API 让日志代码更清晰。

### `pytest` 和 `unittest.mock` 测试框架

`pytest` 是 Python 最流行的测试框架。`unittest.mock` 提供 `MagicMock`（模拟普通对象）和 `AsyncMock`（模拟异步对象），用于在测试中替代真实依赖。

```python
import pytest
from unittest.mock import MagicMock, AsyncMock

def test_basic():
    mock_db = MagicMock()
    mock_db.query.return_value = [1, 2, 3]
    assert len(mock_db.query()) == 3

@pytest.mark.asyncio
async def test_async():
    mock_client = AsyncMock()
    mock_client.fetch.return_value = "data"
    result = await mock_client.fetch()
    assert result == "data"
```

**为什么在本课中使用：** 测试心跳服务时，不可能真正启动 LLM 提供者。用 `MagicMock` 模拟 `ProviderManager`，用 `AsyncMock` 模拟异步调用，实现隔离测试。

### `str.join()` 和列表推导

`str.join()` 把一个字符串列表用指定分隔符连接成一个字符串。列表推导可以在一行内从可迭代对象生成新列表。

```python
lines = ["[Unit]", "Description=MyApp", "[Service]", "Type=simple"]
content = "\n".join(lines)

# 列表推导
squares = [x ** 2 for x in range(5)]  # [0, 1, 4, 9, 16]
```

**为什么在本课中使用：** 生成 systemd unit 文件时，先把各配置行放入列表，再用 `"\n".join(lines)` 拼接成最终文件内容。

### `f-string` 格式化字符串

f-string（`f"...{expression}..."`）是 Python 3.6+ 引入的字符串格式化方式，可以在字符串中直接嵌入变量或表达式。

```python
name = "Ultrabot"
port = 8080
print(f"启动 {name}，监听端口 {port}")  # 启动 Ultrabot，监听端口 8080
```

**为什么在本课中使用：** 代码中大量使用 f-string 来构建服务文件内容、日志消息和错误信息，简洁且可读性强。
