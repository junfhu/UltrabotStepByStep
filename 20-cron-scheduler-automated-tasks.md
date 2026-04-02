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
