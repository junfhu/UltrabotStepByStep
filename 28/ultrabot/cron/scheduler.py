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
