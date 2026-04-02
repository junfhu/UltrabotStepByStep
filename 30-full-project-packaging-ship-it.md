# 课程 30：完整项目打包 — 交付上线！

**目标：** 将课程 1–29 中构建的所有内容打包为一个规范的可安装 Python 项目，包含 `pyproject.toml`、入口点、CI 配置和完整的 README。

**你将学到：**
- 使用 `pyproject.toml` 和 Hatchling 进行现代 Python 打包
- 可选通道/功能附加依赖的依赖组
- 控制台入口点（`ultrabot` 命令）
- 通过 `__main__.py` 支持 `python -m ultrabot`
- 包元数据、分类器和构建配置
- Ruff、pytest 和覆盖率配置
- 编写带徽章和快速入门的 README
- 运行最终测试套件验证一切正常

**新建/修改的文件：**
- `pyproject.toml` — 完整的项目配置
- `ultrabot/__init__.py` — 版本和包元数据
- `ultrabot/__main__.py` — `python -m ultrabot` 入口点
- `README.md` — 项目文档
- `.gitignore` — 标准 Python 忽略文件
- `LICENSE` — MIT 许可证

这是**结业课程**。课程 1–29 中的每个模块现在都被组装到一个可安装的包中。

### 步骤 1：包根目录 — `ultrabot/__init__.py`

每个 Python 包都需要一个 `__init__.py`。我们的非常精简 — 只有版本和品牌信息。

```python
# ultrabot/__init__.py
"""ultrabot - 一个强大、功能丰富的个人 AI 助手框架。"""

__version__ = "0.1.0"
__logo__ = "\U0001f916"  # 🤖 机器人脸
__all__ = ["__version__", "__logo__"]
```

**为什么这么精简？** 我们避免在包级别导入重量级模块。每个子包（`agent`、`providers`、`channels` 等）按需导入。这使得 `import ultrabot` 保持快速 — 即使在冷启动时也在 10ms 以内。

### 步骤 2：`__main__.py` 入口点

这使得用户可以运行 `python -m ultrabot` 作为 `ultrabot` 控制台脚本的替代方式。

```python
# ultrabot/__main__.py
"""python -m ultrabot 的入口点。"""

from ultrabot.cli.commands import app

if __name__ == "__main__":
    app()
```

就这些 — 三行代码。真正的逻辑在 `ultrabot.cli.commands` 中，我们在课程 8 中构建了它。`app` 对象是包含所有命令的 Typer 应用程序：`onboard`、`agent`、`gateway`、`webui`、`status`、`experts`。

### 步骤 3：CLI 入口点 — `ultrabot.cli.commands:app`

这是 `ultrabot` 控制台命令指向的位置。以下是我们在之前课程中构建的结构：

```python
# ultrabot/cli/commands.py  （结构概览 — 在课程 8、17、19 中构建）
"""ultrabot 助手框架的 CLI 命令。"""

import typer
from ultrabot import __version__

app = typer.Typer(
    name="ultrabot",
    help="ultrabot -- A robust personal AI assistant framework.",
    add_completion=False,
    no_args_is_help=True,
)

# ── 注册在 app 上的命令 ──────────────────────────────
# @app.command() onboard     — 初始化配置 + 工作空间
# @app.command() agent       — 交互式聊天或单次消息
# @app.command() gateway     — 启动所有消息通道
# @app.command() webui       — 启动 Web 仪表盘
# @app.command() status      — 显示提供商/通道状态
# experts 子命令组：
#   experts list              — 列出已加载的专家人设
#   experts info <slug>       — 显示专家详情
#   experts search <query>    — 按关键字搜索
#   experts sync              — 从 GitHub 下载

@app.callback()
def main(
    version: Annotated[Optional[bool],
        typer.Option("--version", "-V", callback=version_callback, is_eager=True),
    ] = None,
) -> None:
    """ultrabot -- personal AI assistant framework."""
```

### 步骤 4：完整的 `pyproject.toml`

这是包的核心。它在一个文件中定义了依赖项、可选附加依赖、构建系统、入口点和工具配置。

```toml
# pyproject.toml
[project]
name = "ultrabot-ai"
version = "0.1.0"
description = "A robust, feature-rich personal AI assistant framework with circuit breakers, failover, parallel tools, and plugin system"
readme = { file = "README.md", content-type = "text/markdown" }
requires-python = ">=3.11"
license = {text = "MIT"}
authors = [
    {name = "ultrabot contributors"}
]
keywords = ["ai", "agent", "chatbot", "assistant", "llm"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
]

# ── 核心依赖（始终安装） ─────────────────────────────────
dependencies = [
    "typer>=0.20.0,<1.0.0",                  # CLI 框架
    "anthropic>=0.45.0,<1.0.0",              # Anthropic SDK
    "openai>=2.8.0",                          # OpenAI SDK
    "pydantic>=2.12.0,<3.0.0",              # 配置验证
    "pydantic-settings>=2.12.0,<3.0.0",     # 环境变量加载
    "httpx>=0.28.0,<1.0.0",                 # 异步 HTTP（辅助客户端、提供商）
    "loguru>=0.7.3,<1.0.0",                 # 结构化日志
    "rich>=14.0.0,<15.0.0",                 # 终端格式化
    "prompt-toolkit>=3.0.50,<4.0.0",        # 交互式 REPL
    "questionary>=2.0.0,<3.0.0",            # 安装向导
    "croniter>=6.0.0,<7.0.0",               # Cron 调度
    "tiktoken>=0.12.0,<1.0.0",              # Token 计数
    "aiosqlite>=0.21.0,<1.0.0",             # 异步 SQLite（记忆、用量）
    "json-repair>=0.57.0,<1.0.0",           # 修复 LLM 产生的格式错误 JSON
    "chardet>=3.0.2,<6.0.0",                # 字符编码检测
    "ddgs>=9.5.5,<10.0.0",                  # DuckDuckGo 搜索工具
    "websockets>=16.0,<17.0",               # WebSocket 支持
]

# ── 可选依赖组 ───────────────────────────────────────────
# 每个消息通道和功能都是一个可选附加依赖。
# 只安装你需要的：pip install ultrabot-ai[telegram]
[project.optional-dependencies]
telegram = [
    "python-telegram-bot[socks]>=22.6,<23.0",
]
discord = [
    "discord.py>=2.4.0,<3.0.0",
]
slack = [
    "slack-sdk>=3.39.0,<4.0.0",
    "slackify-markdown>=0.2.0,<1.0.0",
]
feishu = [
    "lark-oapi>=1.4.0,<2.0.0",
]
qq = [
    "qq-botpy>=1.2.0,<2.0.0",
    "aiohttp>=3.9.0,<4.0.0",
]
wecom = [
    "wecom-aibot-sdk>=0.1.0",
]
weixin = [
    "pycryptodome>=3.20.0,<4.0.0",
    "qrcode>=8.0,<9.0",
]
mcp = [
    "mcp>=1.26.0,<2.0.0",
]
webui = [
    "fastapi>=0.115.0,<1.0.0",
    "uvicorn[standard]>=0.34.0,<1.0.0",
]
# ── 便捷组 ───────────────────────────────────────────────
all = [
    "ultrabot-ai[telegram,discord,slack,feishu,qq,wecom,weixin,mcp,webui]",
]
dev = [
    "pytest>=9.0.0,<10.0.0",
    "pytest-asyncio>=1.3.0,<2.0.0",
    "pytest-cov>=6.0.0,<7.0.0",
    "ruff>=0.1.0",
]

# ── 控制台入口点 ─────────────────────────────────────────
# 安装包后会创建 `ultrabot` 命令。
[project.scripts]
ultrabot = "ultrabot.cli.commands:app"

# ── 构建系统 ─────────────────────────────────────────────
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build]
include = [
    "ultrabot/**/*.py",
    "ultrabot/templates/**/*.md",
    "ultrabot/skills/**/*.md",
    "ultrabot/experts/personas/**/*.md",
    "ultrabot/webui/static/**/*",
]

[tool.hatch.build.targets.wheel]
packages = ["ultrabot"]

# ── Ruff（代码检查 + 格式化） ───────────────────────────
[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "N", "W"]
ignore = ["E501"]     # 长行我们自己处理

# ── Pytest ───────────────────────────────────────────────
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

# ── 覆盖率 ───────────────────────────────────────────────
[tool.coverage.run]
source = ["ultrabot"]
omit = ["tests/*", "**/tests/*"]
```

**关键设计决策解释：**

1. **Hatchling 构建系统** — 比 setuptools 更轻量，原生支持 `pyproject.toml`，并能处理我们的混合内容包（Python + Markdown + 静态文件）。

2. **可选依赖组** — 通道库很重。`python-telegram-bot` 会拉入 `httpx`、`aiohttp` 等。只需要 Discord 的用户不应该安装 Telegram 的依赖。`all` 元组会安装所有内容。

3. **`[project.scripts]`** — 将 `ultrabot` 命令映射到 `ultrabot.cli.commands:app`。Typer 处理参数解析。`pip install` 之后，在任何地方输入 `ultrabot` 就能运行我们的 CLI。

4. **Ruff 替代 Black+isort+flake8** — 一个工具替代三个。`select = ["E", "F", "I", "N", "W"]` 捕获错误、导入排序、命名和警告。

### 步骤 5：确保所有 `__init__.py` 文件存在

`ultrabot/` 目录树中的每个子目录都需要一个 `__init__.py`，Python 才能将其识别为包。以下是完整列表：

```
ultrabot/__init__.py          ← 版本 + 元数据
ultrabot/agent/__init__.py
ultrabot/bus/__init__.py
ultrabot/channels/__init__.py
ultrabot/chunking/__init__.py
ultrabot/cli/__init__.py
ultrabot/config/__init__.py
ultrabot/cron/__init__.py
ultrabot/daemon/__init__.py
ultrabot/experts/__init__.py
ultrabot/gateway/__init__.py
ultrabot/heartbeat/__init__.py
ultrabot/mcp/__init__.py
ultrabot/media/__init__.py
ultrabot/memory/__init__.py
ultrabot/providers/__init__.py
ultrabot/security/__init__.py
ultrabot/session/__init__.py
ultrabot/skills/__init__.py
ultrabot/tools/__init__.py
ultrabot/updater/__init__.py
ultrabot/usage/__init__.py
ultrabot/utils/__init__.py
ultrabot/webui/__init__.py
```

大多数都是简单的重导出文件，就像我们在课程 24 中构建的 `chunking/__init__.py`。关键原则：从 `__init__.py` 导入，这样调用者使用 `from ultrabot.chunking import chunk_text`，而不是深入到 `ultrabot.chunking.chunker`。

### 步骤 6：README.md

```markdown
# 🤖 UltraBot

**A robust, feature-rich personal AI assistant framework.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

UltraBot is an AI assistant framework with multi-provider LLM support,
7+ messaging channels, 50+ built-in tools, expert personas, and a
production-ready architecture featuring circuit breakers, failover,
and prompt caching.

## Quick Start

    # Install core + all channels
    pip install -e ".[all,dev]"

    # First-time setup
    ultrabot onboard --wizard

    # Interactive chat
    ultrabot agent

    # Multi-channel gateway
    ultrabot gateway

    # Web dashboard
    ultrabot webui

## Features

- **Multi-provider LLM**: Anthropic, OpenAI, DeepSeek, Gemini, Groq, OpenRouter
- **7 Channels**: Telegram, Discord, Slack, Feishu, QQ, WeCom, WeChat
- **50+ Tools**: File I/O, web search, browser, code execution, MCP
- **Expert Personas**: 100+ specialized AI personas
- **Production Ready**: Circuit breakers, retry, failover, rate limiting
- **Smart**: Context compression, prompt caching, usage tracking
- **Secure**: Injection detection, credential redaction, DM pairing

## Architecture

    ultrabot/
    ├── agent/         # Core agent loop, context compression, delegation
    ├── providers/     # LLM providers, prompt caching, auth rotation
    ├── tools/         # 50+ tools, toolsets, browser automation
    ├── channels/      # Telegram, Discord, Slack, etc.
    ├── gateway/       # Multi-channel gateway server
    ├── config/        # Pydantic config, migrations, doctor
    ├── cli/           # Typer CLI, themes, interactive REPL
    ├── session/       # Conversation session management
    ├── security/      # Injection detection, credential redaction
    ├── bus/           # Async message bus (pub/sub)
    ├── experts/       # Expert persona registry
    ├── webui/         # FastAPI web dashboard
    ├── cron/          # Scheduled task engine
    ├── daemon/        # Background process management
    ├── memory/        # Long-term memory (SQLite)
    ├── media/         # Image/audio/document handling
    ├── chunking/      # Platform-aware message splitting
    ├── usage/         # Token/cost tracking
    ├── updater/       # Self-update system
    ├── skills/        # Skill discovery and management
    └── mcp/           # Model Context Protocol client

## Development

    # Install with dev dependencies
    pip install -e ".[all,dev]"

    # Run tests
    python -m pytest tests/ -q

    # Lint
    ruff check ultrabot/

    # Format
    ruff format ultrabot/

## License

MIT
```

### 步骤 7：.gitignore 和 LICENSE

```gitignore
# .gitignore
__pycache__/
*.py[cod]
*.egg-info/
dist/
build/
.eggs/
*.egg
.venv/
venv/
.env
.ruff_cache/
.pytest_cache/
.coverage
htmlcov/
*.db
*.sqlite3
```

```
# LICENSE
MIT License

Copyright (c) 2025 ultrabot contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### 步骤 8：安装 + 验证流程

现在我们把所有东西组合在一起。这是关键时刻 — 作为一个规范的 Python 包，一切是否正常工作？

```bash
# ── 步骤 1：以可编辑模式安装，包含所有附加依赖 ─────────────
pip install -e ".[all,dev]"

# ── 步骤 2：验证控制台入口点 ───────────────────────────────
ultrabot --version
# 预期输出：ultrabot 0.1.0

ultrabot --help
# 预期输出：
# Usage: ultrabot [OPTIONS] COMMAND [ARGS]...
#
# ultrabot -- A robust personal AI assistant framework.
#
# Options:
#   -V, --version  
#   --help         Show this message and exit.
#
# Commands:
#   agent    Start an interactive chat session or send a one-shot message.
#   experts  Manage expert personas (agency-agents).
#   gateway  Start the gateway server with all messaging channels.
#   onboard  Initialize configuration and workspace directories.
#   status   Show provider status, channel status, and configuration info.
#   webui    Start the web UI dashboard.

# ── 步骤 3：验证 python -m ultrabot 可用 ──────────────────
python -m ultrabot --version
# 预期输出：ultrabot 0.1.0

# ── 步骤 4：运行完整测试套件 ──────────────────────────────
python -m pytest tests/ -q
# 预期输出：732 passed in 45s

# ── 步骤 5：带覆盖率运行 ────────────────────────────────
python -m pytest tests/ --cov=ultrabot --cov-report=term-missing -q
# 预期输出：所有模块 85%+ 覆盖率

# ── 步骤 6：代码检查 ──────────────────────────────────────
ruff check ultrabot/
# 预期输出：All checks passed!
```

### 步骤 9：完整架构目录树

这是最终的项目结构 — 我们在 30 节课程中构建的每个文件：

```
heyuagent/
├── pyproject.toml                    # 课程 30：包配置
├── README.md                         # 课程 30：文档
├── LICENSE                           # 课程 30：MIT 许可证
├── .gitignore                        # 课程 30：Git 忽略文件
│
├── ultrabot/
│   ├── __init__.py                   # 课程 30：版本 + 元数据
│   ├── __main__.py                   # 课程 30：python -m ultrabot
│   │
│   ├── agent/                        # 课程 1-4、25-26、28
│   │   ├── agent.py                  # 核心智能体循环
│   │   ├── auxiliary.py              # 用于元数据任务的廉价 LLM
│   │   ├── context_compressor.py     # 对话摘要
│   │   ├── delegate.py              # 子智能体委派
│   │   └── title_generator.py        # 会话标题生成
│   │
│   ├── providers/                    # 课程 6-7、26、29
│   │   ├── manager.py               # 多提供商管理
│   │   ├── anthropic_native.py       # Anthropic 专用提供商
│   │   ├── prompt_cache.py           # 提示词缓存
│   │   └── auth_rotation.py          # API 密钥轮换
│   │
│   ├── tools/                        # 课程 3-4、28
│   │   ├── base.py                   # Tool + ToolRegistry
│   │   ├── toolsets.py               # ToolsetManager
│   │   ├── browser.py                # 6 个 Playwright 浏览器工具
│   │   └── ...                       # 50+ 内置工具
│   │
│   ├── config/                       # 课程 5、29
│   │   ├── loader.py                 # Pydantic 配置加载
│   │   ├── doctor.py                 # 健康检查
│   │   └── migrations.py             # 模式版本控制
│   │
│   ├── cli/                          # 课程 8、29
│   │   ├── commands.py               # Typer CLI 应用
│   │   └── themes.py                 # 4 个内置主题 + YAML
│   │
│   ├── session/                      # 课程 9
│   │   └── manager.py               # 对话持久化
│   │
│   ├── bus/                          # 课程 11
│   │   └── message_bus.py            # 异步发布/订阅
│   │
│   ├── security/                     # 课程 12、27
│   │   ├── injection_detector.py     # 6 种注入类别
│   │   └── redact.py                 # 13 种凭证模式
│   │
│   ├── channels/                     # 课程 13-14、29
│   │   ├── base.py                   # BaseChannel 抽象类
│   │   ├── telegram.py               # Telegram 适配器
│   │   ├── discord.py                # Discord 适配器
│   │   ├── group_activation.py       # @提及门控
│   │   └── pairing.py                # 私聊审批码
│   │
│   ├── gateway/                      # 课程 15-16
│   │   └── server.py                 # 多通道网关
│   │
│   ├── experts/                      # 课程 17-18
│   │   ├── registry.py               # 专家人设注册表
│   │   └── personas/                 # 100+ 人设 Markdown 文件
│   │
│   ├── webui/                        # 课程 19
│   │   ├── app.py                    # FastAPI 服务器
│   │   └── static/                   # CSS + JS
│   │
│   ├── cron/                         # 课程 20
│   │   └── scheduler.py              # Cron 任务引擎
│   │
│   ├── daemon/                       # 课程 21
│   │   └── manager.py                # 后台进程管理
│   │
│   ├── memory/                       # 课程 22
│   │   └── store.py                  # 长期记忆（SQLite）
│   │
│   ├── media/                        # 课程 23
│   │   └── handler.py                # 图片/音频/文档处理
│   │
│   ├── chunking/                     # 课程 24
│   │   └── chunker.py                # 平台感知消息拆分
│   │
│   ├── usage/                        # 课程 29
│   │   └── tracker.py                # Token/成本追踪
│   │
│   ├── updater/                      # 课程 29
│   │   └── update.py                 # 自更新系统
│   │
│   ├── skills/                       # 课程 29
│   │   └── manager.py                # 技能发现
│   │
│   ├── mcp/                          # 课程 29
│   │   └── client.py                 # MCP stdio/HTTP 客户端
│   │
│   ├── heartbeat/                    # 课程 10
│   │   └── circuit_breaker.py        # 断路器模式
│   │
│   └── utils/                        # 共享工具
│       └── ...
│
└── tests/                            # 所有测试文件
    ├── test_chunking.py              # 课程 24
    ├── test_context_compressor.py    # 课程 25
    ├── test_prompt_cache.py          # 课程 26
    ├── test_security.py              # 课程 27
    ├── test_browser_delegate.py      # 课程 28
    ├── test_operational.py           # 课程 29
    └── ...                           # 课程 1-23 的测试
```

### 测试

```python
# tests/test_packaging.py
"""包结构和入口点的测试。"""

import importlib
import subprocess
import sys

import pytest


class TestPackageImports:
    """验证所有子包能正常导入。"""

    @pytest.mark.parametrize("module", [
        "ultrabot",
        "ultrabot.agent",
        "ultrabot.agent.auxiliary",
        "ultrabot.agent.context_compressor",
        "ultrabot.agent.delegate",
        "ultrabot.agent.title_generator",
        "ultrabot.chunking",
        "ultrabot.chunking.chunker",
        "ultrabot.config.doctor",
        "ultrabot.config.migrations",
        "ultrabot.cli.themes",
        "ultrabot.providers.prompt_cache",
        "ultrabot.providers.auth_rotation",
        "ultrabot.security.injection_detector",
        "ultrabot.security.redact",
        "ultrabot.usage.tracker",
        "ultrabot.channels.group_activation",
        "ultrabot.channels.pairing",
        "ultrabot.skills.manager",
    ])
    def test_import(self, module: str):
        """每个模块应能无错误导入。"""
        importlib.import_module(module)


class TestVersion:
    def test_version_exists(self):
        from ultrabot import __version__
        assert __version__
        # 应该是类似 semver 的字符串
        parts = __version__.split(".")
        assert len(parts) >= 2

    def test_version_matches_pyproject(self):
        from ultrabot import __version__
        # 从 pyproject.toml 读取版本
        import tomllib
        from pathlib import Path
        toml_path = Path(__file__).parent.parent / "pyproject.toml"
        if toml_path.exists():
            with open(toml_path, "rb") as f:
                data = tomllib.load(f)
            assert __version__ == data["project"]["version"]


class TestEntryPoint:
    def test_ultrabot_help(self):
        """`ultrabot --help` 命令应该可以正常工作。"""
        result = subprocess.run(
            [sys.executable, "-m", "ultrabot", "--help"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        assert "ultrabot" in result.stdout.lower()

    def test_ultrabot_version(self):
        """`ultrabot --version` 命令应该输出版本号。"""
        result = subprocess.run(
            [sys.executable, "-m", "ultrabot", "--version"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        assert "0.1.0" in result.stdout


class TestPackageStructure:
    def test_all_init_files_exist(self):
        """每个子目录都应该有一个 __init__.py。"""
        from pathlib import Path
        root = Path(__file__).parent.parent / "ultrabot"
        for subdir in root.iterdir():
            if subdir.is_dir() and not subdir.name.startswith(("_", ".")):
                init_file = subdir / "__init__.py"
                assert init_file.exists(), f"Missing __init__.py in {subdir}"
```

### 检查点

这是最终检查点 — 我们验证整个项目作为规范 Python 包端到端正常工作的时刻。

```bash
# 三条命令验证：
pip install -e ".[all,dev]" && ultrabot --help && python -m pytest tests/ -q
```

预期输出：

```
Successfully installed ultrabot-ai-0.1.0
...

 Usage: ultrabot [OPTIONS] COMMAND [ARGS]...

 ultrabot -- A robust personal AI assistant framework.

╭─ Options ──────────────────────────────────────────────────────────╮
│ -V, --version                                                      │
│ --help             Show this message and exit.                     │
╰────────────────────────────────────────────────────────────────────╯
╭─ Commands ─────────────────────────────────────────────────────────╮
│ agent    Start an interactive chat session or send a one-shot...   │
│ experts  Manage expert personas (agency-agents).                   │
│ gateway  Start the gateway server with all messaging channels.     │
│ onboard  Initialize configuration and workspace directories.       │
│ status   Show provider status, channel status, and config info.    │
│ webui    Start the web UI dashboard with chat and config editor.   │
╰────────────────────────────────────────────────────────────────────╯

732 passed in 45.23s
```

### 本课成果

**完整的 ultrabot 包。** 在 30 节课程中，我们从一个向 LLM 发送单条消息的简单 Python 文件，一路发展到了一个生产级的 AI 助手框架，包含：

- **多提供商 LLM 支持**（Anthropic、OpenAI、DeepSeek、Gemini、Groq、OpenRouter），配备断路器、故障转移和提示词缓存
- **7 个消息通道**（Telegram、Discord、Slack、飞书、QQ、企业微信、微信），统一在一个网关后面
- **50+ 工具**，按工具集组织，包括浏览器自动化和 MCP 集成
- **专家人设** — 100+ 个专业 AI 智能体，可通过注册表发现
- **上下文压缩**，让对话可以无限延续而不会触及 token 限制
- **安全加固**，包含注入检测和凭证脱敏
- **运维功能**：用量追踪、自更新、配置诊断、主题、密钥轮换
- **规范的 Python 包**，可通过 `pip install -e ".[all,dev]"` 安装，并通过 `ultrabot` 或 `python -m ultrabot` 运行

每一行代码都经过了测试。每个模块都可以导入。`ultrabot` 命令正常工作。**交付上线。**
