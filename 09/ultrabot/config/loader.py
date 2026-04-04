# ultrabot/config/loader.py
"""配置加载和保存。

规范路径为 ~/.ultrabot/config.json。

取自 ultrabot/config/loader.py。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ultrabot.config.schema import Config


def get_config_path() -> Path:
    """返回默认配置文件路径：~/.ultrabot/config.json。

    取自 ultrabot/config/loader.py 第 39-56 行。
    """
    import os

    env = os.environ.get("ULTRABOT_CONFIG")
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".ultrabot" / "config.json"


def load_config(path: str | Path | None = None) -> Config:
    """加载 ultrabot 配置。

    1. 读取 path（或默认路径）处的 JSON 文件。
    2. 将文件数据作为 init kwargs 传给 Config()。
    3. 环境变量覆盖文件数据（由 settings_customise_sources 保证优先级）。
    4. 如果文件不存在则创建默认配置。

    取自 ultrabot/config/loader.py 第 85-115 行。
    """
    resolved = Path(path).expanduser().resolve() if path else get_config_path()

    file_data: dict[str, Any] = {}
    if resolved.is_file():
        try:
            text = resolved.read_text(encoding="utf-8")
            file_data = json.loads(text)
        except json.JSONDecodeError:
            file_data = {}
    else:
        resolved.parent.mkdir(parents=True, exist_ok=True)

    # file_data 作为 init kwargs 传入；
    # settings_customise_sources 保证 env vars 优先于 init kwargs
    config = Config(**file_data)

    # 写入默认值，让用户有一个起始模板
    if not resolved.is_file():
        save_config(config, resolved)

    return config


def save_config(config: Config, path: str | Path | None = None) -> None:
    """将配置序列化为 JSON 文件。

    取自 ultrabot/config/loader.py 第 118-140 行。
    """
    resolved = Path(path).expanduser().resolve() if path else get_config_path()
    resolved.parent.mkdir(parents=True, exist_ok=True)

    payload = config.model_dump(
        mode="json",
        by_alias=True,      # 在 JSON 中使用 camelCase 键
        exclude_none=True,
    )

    tmp = resolved.with_suffix(".tmp")
    try:
        tmp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        tmp.replace(resolved)  # 原子重命名
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
