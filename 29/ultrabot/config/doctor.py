# ultrabot/config/doctor.py
"""配置诊断工具 -- 检查配置文件健康状态。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from ultrabot.config.migrations import apply_migrations


@dataclass
class HealthCheck:
    """单项健康检查的结果。"""
    name: str = ""
    ok: bool = True
    message: str = ""
    auto_fixable: bool = False


@dataclass
class DoctorReport:
    """所有健康检查的汇总报告。"""
    checks: list[HealthCheck] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return all(c.ok for c in self.checks)


def check_config_file(config_path: Path) -> HealthCheck:
    """检查配置文件是否存在且为合法 JSON。"""
    if not config_path.exists():
        return HealthCheck(
            name="config_file",
            ok=False,
            message=f"Config file not found: {config_path}",
            auto_fixable=True,
        )
    try:
        text = config_path.read_text(encoding="utf-8")
        json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return HealthCheck(
            name="config_file",
            ok=False,
            message=f"Invalid JSON in {config_path}: {e}",
            auto_fixable=True,
        )
    return HealthCheck(name="config_file", ok=True, message="Config file is valid JSON")


def check_providers(config: dict) -> HealthCheck:
    """检查是否配置了至少一个 provider。"""
    providers = config.get("providers", {})
    if not providers:
        return HealthCheck(
            name="providers",
            ok=False,
            message="No providers configured",
            auto_fixable=False,
        )
    return HealthCheck(name="providers", ok=True, message="Providers configured")


def check_config_version(config: dict) -> HealthCheck:
    """检查配置版本是否需要迁移。"""
    from ultrabot.config.migrations import needs_migration, CURRENT_VERSION
    if needs_migration(config):
        return HealthCheck(
            name="config_version",
            ok=False,
            message="Config needs migration",
            auto_fixable=True,
        )
    return HealthCheck(name="config_version", ok=True, message="Config version is current")


def check_workspace(config: dict) -> HealthCheck:
    """检查工作空间路径。"""
    workspace = config.get("workspace")
    if workspace and not Path(workspace).exists():
        return HealthCheck(
            name="workspace",
            ok=False,
            message=f"Workspace directory not found: {workspace}",
            auto_fixable=False,
        )
    return HealthCheck(name="workspace", ok=True, message="Workspace OK")


def check_sessions_dir(data_dir: Path | None) -> HealthCheck:
    """检查会话目录。"""
    if data_dir is None:
        return HealthCheck(name="sessions_dir", ok=True, message="No data dir configured")
    sessions = data_dir / "sessions"
    if not sessions.exists():
        return HealthCheck(
            name="sessions_dir",
            ok=False,
            message=f"Sessions directory not found: {sessions}",
            auto_fixable=True,
        )
    return HealthCheck(name="sessions_dir", ok=True, message="Sessions directory OK")


def check_security(config: dict) -> list[str]:
    """检查安全相关的警告。"""
    warnings: list[str] = []
    for key in ("openai_api_key", "anthropic_api_key"):
        if key in config:
            warnings.append(f"Top-level API key found: {key}. Consider moving to providers section.")
    return warnings


def run_doctor(
    config_path: Path,
    data_dir: Path | None = None,
    repair: bool = False,
) -> DoctorReport:
    """运行所有健康检查并返回报告。"""
    report = DoctorReport()

    report.checks.append(check_config_file(config_path))

    # 如果配置文件存在，读取并进行后续检查
    config: dict = {}
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    report.checks.append(check_config_version(config))
    report.checks.append(check_providers(config))
    report.checks.append(check_workspace(config))
    report.checks.append(check_sessions_dir(data_dir))
    report.warnings = check_security(config)

    if repair:
        apply_migrations(config)

    return report
