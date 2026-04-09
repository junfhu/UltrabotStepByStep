# ultrabot/gateway/__main__.py
"""允许通过以下方式运行：python -m ultrabot.gateway"""

import asyncio
import sys
from pathlib import Path

from ultrabot.config import load_config, get_config_path
from ultrabot.gateway.server import Gateway


def main() -> None:
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else get_config_path()
    if not config_path.exists():
        print(f"Config not found at {config_path}. Run 'ultrabot onboard' first.")
        sys.exit(1)
    cfg = load_config(config_path)
    gw = Gateway(cfg)
    asyncio.run(gw.start())


main()
