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
