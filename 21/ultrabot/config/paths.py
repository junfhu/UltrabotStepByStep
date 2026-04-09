# ultrabot/config/paths.py
"""文件系统路径辅助函数。

所有目录在首次访问时延迟创建。

取自 ultrabot/config/paths.py。
"""
from __future__ import annotations

from pathlib import Path

_DATA_DIR_NAME = ".ultrabot"


def _ensure_dir(path: Path) -> Path:
    """如果需要则创建路径及其父目录，然后返回它。"""
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_data_dir() -> Path:
    """~/.ultrabot -- 首次访问时创建。"""
    return _ensure_dir(Path.home() / _DATA_DIR_NAME)


def get_workspace_path(workspace: str | None = None) -> Path:
    """解析并返回工作区目录。"""
    if workspace is None:
        return _ensure_dir(get_data_dir() / "workspace")
    return _ensure_dir(Path(workspace).expanduser().resolve())


def get_cli_history_path() -> Path:
    """~/.ultrabot/cli_history。"""
    return get_data_dir() / "cli_history"
