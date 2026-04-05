# ultrabot/gateway/__main__.py
"""允许通过以下方式运行：python -m ultrabot.gateway"""

import asyncio
import os
import re

from ultrabot.config.loader import load_config


def _expand_env_vars(obj):
    """递归展开配置值中的 ${ENV_VAR} 引用。"""
    if isinstance(obj, str):
        return re.sub(
            r"\$\{(\w+)\}",
            lambda m: os.environ.get(m.group(1), m.group(0)),
            obj,
        )
    if isinstance(obj, dict):
        return {k: _expand_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env_vars(item) for item in obj]
    return obj


def main() -> None:
    config = load_config()
    channels_cfg = _expand_env_vars(config.channels)

    from ultrabot.gateway.server import Gateway
    gateway = Gateway(config, channels_cfg)
    asyncio.run(gateway.start())


main()
