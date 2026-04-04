# Ultrabot：30 课程开发指南
**从零开始构建一个生产级 AI 助手框架。**
本指南将带你从"向 LLM 问好"一步步走到一个完整的多提供者、多通道 AI 智能体，具备工具调用、记忆、安全防护和 Web 界面。每节课程都建立在上一节课的基础之上。每节课都包含可运行的代码和测试。  
本教程的主要思路来自于
- Nanobot (https://github.com/HKUDS/nanobot)
- Learn-Claude-Code (https://github.com/shareAI-lab/learn-claude-code/)

本课程设计由AI辅助下完成，因为课程自身也在不停修正，请参考 https://github.com/junfhu/UltrabotStepByStep，如果您觉得对您有帮助，请帮助点亮一颗星。  
本课程中使用的大模型提供商是火山引擎Code Plan，如果正好你也需要，可以使用我的邀请码获取9折优惠 https://volcengine.com/L/_01BJCkKdMc/  邀请码：HHCDB4J4）  



# 课程 20：定时任务调度器 — 自动化任务

**目标：** 构建一个基于时间的任务调度器，按 cron 表达式通过消息总线触发消息。

**你将学到：**
- 使用标准 cron 表达式的 `CronJob` dataclass
- 带有逐秒 tick 循环的 `CronScheduler`
- 基于 JSON 的任务持久化到磁盘
- 集成 `croniter` 计算下次运行时间
- 通过 `MessageBus` 发布调度消息

**新建文件：**
- `ultrabot/cron/__init__.py` — 包导出
- `ultrabot/cron/scheduler.py` — 定时任务管理和调度循环

### 步骤 1：CronJob Dataclass

每个任务包含一个 cron 表达式、要发送的消息和目标通道。

```python
# ultrabot/cron/scheduler.py
"""定时任务调度器 -- 基于时间的自动消息分发。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

try:
    from croniter import croniter          # pip install croniter
    _CRONITER_AVAILABLE = True
except ImportError:
    _CRONITER_AVAILABLE = False

if TYPE_CHECKING:
    from ultrabot.bus.queue import MessageBus


def _require_croniter() -> None:
    """守卫：如果 croniter 未安装则抛出有帮助的错误信息。"""
    if not _CRONITER_AVAILABLE:
        raise ImportError(
            "croniter is required for cron scheduling. "
            "Install it with:  pip install croniter"
        )


@dataclass
class CronJob:
    """表示单个调度定时任务。

    属性：
        name: 唯一的任务标识符。
        schedule: 标准 cron 表达式（例如 "0 9 * * *" = 每天上午 9 点）。
        message: 任务触发时在总线上发布的文本。
        channel: 目标通道名称（例如 "telegram"、"discord"）。
        chat_id: 目标聊天/通道 ID。
        enabled: 任务是否处于活跃状态。
    """
    name: str
    schedule: str           # "0 9 * * *"  = 每天 09:00 UTC
    message: str            # 任务触发时发送的文本
    channel: str            # 目标通道
    chat_id: str            # 目标聊天 ID
    enabled: bool = True
    _next_run: datetime | None = field(default=None, repr=False, compare=False)

    def compute_next(self, now: datetime | None = None) -> datetime:
        """从 *now* 计算并缓存下次运行时间。"""
        _require_croniter()
        now = now or datetime.now(timezone.utc)
        cron = croniter(self.schedule, now)
        self._next_run = cron.get_next(datetime).replace(tzinfo=timezone.utc)
        return self._next_run
```

### 步骤 2：CronScheduler

调度器从 JSON 文件加载任务，运行逐秒检查循环，并在任务到期时向总线发布消息。

```python
class CronScheduler:
    """从 JSON 文件加载定时任务并按计划触发。

    *cron_dir* 中的每个 ``*.json`` 文件描述一个 CronJob。
    调度器每秒检查一次是否有任务到期，如果有，
    就将该任务的消息发布到 MessageBus。
    """

    def __init__(self, cron_dir: Path, bus: "MessageBus") -> None:
        self._cron_dir = cron_dir
        self._bus = bus
        self._jobs: dict[str, CronJob] = {}
        self._task: asyncio.Task[None] | None = None
        self._running = False

    # -- 任务管理 ---------------------------------------------------

    def load_jobs(self) -> None:
        """扫描 cron_dir 中的 *.json 文件并将每个加载为 CronJob。"""
        self._cron_dir.mkdir(parents=True, exist_ok=True)
        count = 0
        for path in sorted(self._cron_dir.glob("*.json")):
            try:
                job = self._load_job_file(path)
                self._jobs[job.name] = job
                count += 1
            except Exception:
                logger.exception("Failed to load cron job from {}", path)
        logger.info("Loaded {} cron job(s) from {}", count, self._cron_dir)

    @staticmethod
    def _load_job_file(path: Path) -> CronJob:
        data = json.loads(path.read_text(encoding="utf-8"))
        job = CronJob(
            name=data["name"],
            schedule=data["schedule"],
            message=data["message"],
            channel=data["channel"],
            chat_id=str(data["chat_id"]),
            enabled=data.get("enabled", True),
        )
        job.compute_next()
        return job

    def add_job(self, job: CronJob) -> None:
        """注册任务并持久化到磁盘。"""
        job.compute_next()
        self._jobs[job.name] = job
        self._persist_job(job)
        logger.info("Cron job '{}' added (schedule={})", job.name, job.schedule)

    def remove_job(self, name: str) -> None:
        """从调度器和磁盘中移除任务。"""
        if name in self._jobs:
            del self._jobs[name]
        path = self._cron_dir / f"{name}.json"
        if path.exists():
            path.unlink()
        logger.info("Cron job '{}' removed", name)

    def _persist_job(self, job: CronJob) -> None:
        path = self._cron_dir / f"{job.name}.json"
        data = {
            "name": job.name, "schedule": job.schedule, "message": job.message,
            "channel": job.channel, "chat_id": job.chat_id, "enabled": job.enabled,
        }
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # -- 生命周期 --------------------------------------------------------

    async def start(self) -> None:
        """启动后台调度循环。"""
        if not self._jobs:
            logger.debug("No cron jobs loaded -- scheduler idle")
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="cron-scheduler")
        logger.info("Cron scheduler started ({} job(s))", len(self._jobs))

    async def stop(self) -> None:
        """取消后台任务。"""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Cron scheduler stopped")

    # -- 内部循环 ----------------------------------------------------

    async def _loop(self) -> None:
        """每秒检查是否有任务到期。"""
        while self._running:
            now = datetime.now(timezone.utc)
            for job in list(self._jobs.values()):
                if not job.enabled:
                    continue
                if job._next_run is None:
                    job.compute_next(now)
                    continue
                if now >= job._next_run:
                    await self._fire(job)
                    job.compute_next(now)
            await asyncio.sleep(1)

    async def _fire(self, job: CronJob) -> None:
        """将任务的消息发布到总线。"""
        from ultrabot.bus.events import InboundMessage

        logger.info("Cron job '{}' fired", job.name)
        msg = InboundMessage(
            channel=job.channel,
            sender_id="cron",
            chat_id=job.chat_id,
            content=job.message,
            metadata={"cron_job": job.name},
        )
        await self._bus.publish(msg)
```

### 测试

```python
# tests/test_cron_scheduler.py
"""定时任务调度器的测试。"""

import json
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from ultrabot.cron.scheduler import CronJob, CronScheduler


class TestCronJob:
    def test_create_job(self):
        job = CronJob(
            name="daily-summary",
            schedule="0 9 * * *",
            message="Generate daily summary",
            channel="telegram",
            chat_id="123456",
        )
        assert job.name == "daily-summary"
        assert job.enabled is True

    @pytest.mark.skipif(
        not __import__("importlib").util.find_spec("croniter"),
        reason="croniter not installed",
    )
    def test_compute_next(self):
        job = CronJob(
            name="test", schedule="0 * * * *",  # 每小时
            message="ping", channel="test", chat_id="1",
        )
        now = datetime(2025, 1, 15, 10, 30, tzinfo=timezone.utc)
        next_run = job.compute_next(now)
        assert next_run.hour == 11
        assert next_run.minute == 0


class TestCronScheduler:
    def test_load_jobs_from_dir(self, tmp_path):
        # 写入一个任务 JSON 文件
        job_data = {
            "name": "test-job",
            "schedule": "*/5 * * * *",
            "message": "Hello from cron",
            "channel": "telegram",
            "chat_id": "12345",
            "enabled": True,
        }
        (tmp_path / "test-job.json").write_text(json.dumps(job_data))

        bus = MagicMock()
        scheduler = CronScheduler(cron_dir=tmp_path, bus=bus)
        scheduler.load_jobs()
        assert "test-job" in scheduler._jobs

    def test_add_and_remove_job(self, tmp_path):
        bus = MagicMock()
        scheduler = CronScheduler(cron_dir=tmp_path, bus=bus)

        job = CronJob(
            name="new-job", schedule="0 12 * * *",
            message="Noon check", channel="slack", chat_id="C123",
        )
        scheduler.add_job(job)
        assert "new-job" in scheduler._jobs
        assert (tmp_path / "new-job.json").exists()

        scheduler.remove_job("new-job")
        assert "new-job" not in scheduler._jobs
        assert not (tmp_path / "new-job.json").exists()

    @pytest.mark.asyncio
    async def test_fire_publishes_to_bus(self, tmp_path):
        bus = AsyncMock()
        scheduler = CronScheduler(cron_dir=tmp_path, bus=bus)

        job = CronJob(
            name="fire-test", schedule="* * * * *",
            message="Test fire", channel="test", chat_id="1",
        )
        await scheduler._fire(job)
        bus.publish.assert_called_once()
        msg = bus.publish.call_args[0][0]
        assert msg.content == "Test fire"
        assert msg.metadata == {"cron_job": "fire-test"}

    @pytest.mark.asyncio
    async def test_start_stop(self, tmp_path):
        bus = AsyncMock()
        scheduler = CronScheduler(cron_dir=tmp_path, bus=bus)
        await scheduler.start()
        assert scheduler._running is True
        await scheduler.stop()
        assert scheduler._running is False
```

### 检查点

```bash
python -c "
import json, tempfile
from pathlib import Path
from unittest.mock import MagicMock

from ultrabot.cron.scheduler import CronJob, CronScheduler

# 创建一个带有任务的临时 cron 目录
cron_dir = Path(tempfile.mkdtemp())
job = {
    'name': 'morning-greeting',
    'schedule': '0 8 * * *',
    'message': 'Good morning! Time for your daily briefing.',
    'channel': 'telegram',
    'chat_id': '123456',
}
(cron_dir / 'morning-greeting.json').write_text(json.dumps(job))

bus = MagicMock()
scheduler = CronScheduler(cron_dir=cron_dir, bus=bus)
scheduler.load_jobs()

for name, j in scheduler._jobs.items():
    print(f'Job: {name}')
    print(f'  Schedule: {j.schedule}')
    print(f'  Message: {j.message}')
    print(f'  Next run: {j._next_run}')
    print(f'  Enabled: {j.enabled}')
"
```

预期输出：
```
Job: morning-greeting
  Schedule: 0 8 * * *
  Message: Good morning! Time for your daily briefing.
  Next run: 2025-XX-XX 08:00:00+00:00
  Enabled: True
```

### 本课成果

一个定时任务调度器，从 JSON 文件加载任务定义，使用 `croniter` 计算下次运行时间，
并按计划向消息总线发布消息。任务持久化到磁盘，可在重启后恢复。调度器作为
asyncio 后台任务运行，每秒检查一次。

---

## 本课使用的 Python 知识

### `@dataclass` 与 `field()` 控制参数

`@dataclass` 自动生成构造函数和常用方法。`field()` 可以精细控制每个字段的行为：`default` 设置默认值，`repr=False` 从打印输出中隐藏字段，`compare=False` 让字段不参与相等性比较。

```python
from dataclasses import dataclass, field

@dataclass
class Task:
    name: str
    schedule: str
    _internal: str = field(default="", repr=False, compare=False)

t = Task(name="backup", schedule="0 2 * * *")
print(t)  # Task(name='backup', schedule='0 2 * * *')  -- _internal 被隐藏
```

**为什么在本课中使用：** `CronJob` 的 `_next_run` 字段是内部计算的缓存值，不应该在打印时显示，也不应该影响两个任务的相等性比较。`field(default=None, repr=False, compare=False)` 精确控制了这些行为。

### `asdict()`（数据类转字典）

`dataclasses.asdict()` 将数据类实例递归转换为字典，方便序列化为 JSON 或传递给其他接口。

```python
from dataclasses import dataclass, asdict

@dataclass
class Config:
    host: str = "localhost"
    port: int = 8080

cfg = Config()
print(asdict(cfg))  # {'host': 'localhost', 'port': 8080}
```

**为什么在本课中使用：** 虽然 `_persist_job` 手动构建了字典（为了排除私有字段 `_next_run`），`asdict` 仍是 `dataclass` 生态中常用的工具，在模块的导入列表中被引入以备使用。

### `datetime` 与 `timezone.utc`（时区感知的日期时间）

`datetime` 模块处理日期和时间。`timezone.utc` 是 UTC 时区对象，与它结合使用可以创建时区感知的时间戳，避免时区混淆的 bug。

```python
from datetime import datetime, timezone

now = datetime.now(timezone.utc)  # 当前 UTC 时间（时区感知）
print(now)  # 2025-01-15 10:30:00+00:00

# 比较两个时间
future = datetime(2025, 12, 31, tzinfo=timezone.utc)
if now < future:
    print("还没到年底")
```

**为什么在本课中使用：** 定时任务需要精确的时间比较来判断任务是否到期。统一使用 `timezone.utc` 确保所有时间计算基于同一时区，避免了服务器时区不同导致的调度错误。

### `try` / `except ImportError`（可选依赖守卫）

通过 `try/except ImportError` 检测可选依赖是否已安装，并设置一个布尔标志。在实际使用时通过守卫函数检查标志，给出友好的安装提示。

```python
try:
    from croniter import croniter
    _CRONITER_AVAILABLE = True
except ImportError:
    _CRONITER_AVAILABLE = False

def _require_croniter():
    if not _CRONITER_AVAILABLE:
        raise ImportError(
            "croniter is required. Install: pip install croniter"
        )
```

**为什么在本课中使用：** `croniter` 是计算 cron 下次执行时间的库，但不是核心依赖。模块可以被导入和加载（用于其他功能），只有在实际创建定时任务时才需要 `croniter`。守卫模式延迟了错误到真正需要时才抛出。

### `asyncio.create_task()`（创建后台异步任务）

`asyncio.create_task()` 将一个协程包装为一个 `Task` 对象并立即开始执行。它在后台运行，不会阻塞当前代码流。

```python
import asyncio

async def background_loop():
    while True:
        print("tick")
        await asyncio.sleep(1)

async def main():
    task = asyncio.create_task(background_loop(), name="ticker")
    await asyncio.sleep(5)  # 主程序继续做其他事
    task.cancel()           # 5 秒后取消后台任务

asyncio.run(main())
```

**为什么在本课中使用：** `CronScheduler.start()` 用 `asyncio.create_task(self._loop())` 启动调度循环作为后台任务。调度器在后台每秒检查一次是否有任务到期，不阻塞主程序的其他操作（如处理消息）。

### `asyncio.CancelledError`（任务取消异常）

当一个 `asyncio.Task` 被 `cancel()` 取消时，会在任务内部抛出 `CancelledError` 异常。捕获它可以实现优雅的清理逻辑。

```python
import asyncio

async def worker():
    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        print("任务被取消，执行清理...")
        raise  # 重新抛出以确认取消

task = asyncio.create_task(worker())
task.cancel()
try:
    await task
except asyncio.CancelledError:
    pass  # 预期中的取消
```

**为什么在本课中使用：** `CronScheduler.stop()` 调用 `self._task.cancel()` 取消调度循环，然后用 `try/except asyncio.CancelledError` 捕获取消异常，确保调度器优雅关闭而不是崩溃。

### `asyncio.sleep()`（异步等待）

`asyncio.sleep(seconds)` 暂停当前协程指定的秒数，但不阻塞事件循环——其他协程可以在这段时间内继续运行。

```python
import asyncio

async def countdown(n):
    for i in range(n, 0, -1):
        print(i)
        await asyncio.sleep(1)  # 等 1 秒，但不阻塞其他任务
    print("发射！")
```

**为什么在本课中使用：** 调度循环用 `await asyncio.sleep(1)` 实现每秒一次的 tick。与 `time.sleep(1)` 不同，它不会阻塞事件循环，消息处理和其他通道可以在间隔期间正常工作。

### `json.loads()` / `json.dumps()`（JSON 持久化）

`json.loads()` 将 JSON 字符串解析为 Python 对象，`json.dumps()` 将 Python 对象序列化为 JSON 字符串。`indent` 参数生成格式化的可读输出。

```python
import json

job = {"name": "backup", "schedule": "0 2 * * *", "enabled": True}
json_str = json.dumps(job, indent=2)  # 格式化 JSON
print(json_str)

loaded = json.loads(json_str)  # 解析回 Python 字典
print(loaded["name"])  # "backup"
```

**为什么在本课中使用：** 定时任务存储为 JSON 文件（每个任务一个 `.json` 文件）。`_persist_job()` 用 `json.dumps(data, indent=2)` 写入可读的 JSON，`_load_job_file()` 用 `json.loads()` 从文件读取任务定义。

### `Path.glob()` 与文件操作

`Path.glob("*.json")` 匹配目录中符合模式的所有文件。`Path.unlink()` 删除文件。这些方法让文件操作变得简洁。

```python
from pathlib import Path

config_dir = Path("/etc/myapp")
for f in sorted(config_dir.glob("*.json")):  # 所有 JSON 文件
    print(f.name)

# 删除文件
(config_dir / "old.json").unlink()
```

**为什么在本课中使用：** `load_jobs()` 用 `glob("*.json")` 扫描 cron 目录中的所有任务文件。`remove_job()` 用 `path.unlink()` 删除任务文件。`mkdir(parents=True, exist_ok=True)` 确保 cron 目录存在。

### `@staticmethod`（静态方法）

`@staticmethod` 定义不需要访问实例（`self`）或类（`cls`）的方法，本质上是类命名空间中的普通函数。

```python
class FileLoader:
    @staticmethod
    def load(path):
        with open(path) as f:
            return f.read()

content = FileLoader.load("data.txt")  # 无需创建实例
```

**为什么在本课中使用：** `CronScheduler._load_job_file()` 是一个纯函数——接收文件路径，返回 `CronJob` 对象，不需要访问调度器实例。定义为 `@staticmethod` 清楚地表达了这种独立性。

### `list()` 包装迭代器（安全遍历）

在遍历字典的 `.values()` 时如果会修改字典（增删元素），需要先用 `list()` 创建副本，避免 `RuntimeError: dictionary changed size during iteration`。

```python
data = {"a": 1, "b": 2, "c": 3}

# 危险：遍历时修改
# for k, v in data.items():
#     if v < 2: del data[k]  # RuntimeError!

# 安全：先创建副本
for k in list(data.keys()):
    if data[k] < 2:
        del data[k]
```

**为什么在本课中使用：** `_loop()` 中 `for job in list(self._jobs.values())` 创建了任务列表的快照。如果在循环中有任务被添加或移除，使用副本可以避免字典在遍历期间改变大小的错误。

### `TYPE_CHECKING` 模式（条件导入）

`typing.TYPE_CHECKING` 在运行时为 `False`，仅在类型检查工具运行时为 `True`。用于避免运行时的循环导入，同时保留类型提示。

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mypackage.bus import MessageBus  # 仅类型检查时导入

class Scheduler:
    def __init__(self, bus: "MessageBus"):  # 用字符串注解
        self._bus = bus
```

**为什么在本课中使用：** `CronScheduler` 需要引用 `MessageBus` 类型，但不想在导入时立刻加载整个消息总线模块。`TYPE_CHECKING` 模式让类型注解正常工作，同时避免不必要的运行时依赖。

### `MagicMock` / `AsyncMock`（测试模拟对象）

`unittest.mock` 的 `MagicMock` 创建可以模拟任何对象的假对象，`AsyncMock` 专门用于模拟异步方法。它们自动记录被调用的方式，方便验证。

```python
from unittest.mock import AsyncMock, MagicMock

bus = AsyncMock()
await bus.publish(some_message)

bus.publish.assert_called_once()  # 验证被调用了一次
msg = bus.publish.call_args[0][0]  # 获取调用时的第一个参数
```

**为什么在本课中使用：** 测试定时任务时不需要真正的消息总线。`AsyncMock()` 模拟 `MessageBus`，让 `_fire()` 可以正常 `await bus.publish(msg)`，测试则通过 `assert_called_once()` 验证消息确实被发布了。

### `@pytest.mark.skipif`（条件跳过测试）

`@pytest.mark.skipif(condition, reason="...")` 在条件为真时跳过测试。常用于跳过依赖特定库或环境的测试。

```python
import pytest
import importlib

@pytest.mark.skipif(
    not importlib.util.find_spec("croniter"),
    reason="croniter not installed"
)
def test_compute_next():
    # 这个测试只在 croniter 已安装时运行
    from croniter import croniter
    # ...
```

**为什么在本课中使用：** `test_compute_next()` 依赖 `croniter` 库来计算下次运行时间。如果 `croniter` 未安装，测试会被优雅地跳过而非报错，确保测试套件在不同环境中都能正常运行。

### `@pytest.mark.asyncio`（异步测试）

`@pytest.mark.asyncio` 标记让 pytest 可以运行 `async def` 测试函数。pytest-asyncio 插件自动管理事件循环的创建和销毁。

```python
import pytest

@pytest.mark.asyncio
async def test_async_operation():
    result = await some_async_function()
    assert result == "expected"
```

**为什么在本课中使用：** `CronScheduler` 的 `start()`、`stop()`、`_fire()` 都是异步方法。测试它们必须在异步上下文中运行，`@pytest.mark.asyncio` 提供了这个环境。
