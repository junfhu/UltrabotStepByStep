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
