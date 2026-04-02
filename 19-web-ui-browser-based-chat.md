# 课程 19：Web 界面 — 基于浏览器的聊天

**目标：** 构建一个 FastAPI 后端，包含 REST 端点和 WebSocket 流式传输，提供基于浏览器的聊天界面。

**你将学到：**
- FastAPI 应用工厂模式与启动生命周期
- 用于健康检查、提供者、会话、工具和配置的 REST 端点
- 带有内容增量和工具通知的 WebSocket 流式传输
- 将配置 schema 桥接到组件接口的适配器模式
- 支持 SPA 的静态文件服务

**新建文件：**
- `ultrabot/webui/__init__.py` — 包标记
- `ultrabot/webui/app.py` — FastAPI 应用工厂、REST API、WebSocket 聊天

### 步骤 1：应用工厂和适配器类

Web 界面需要将 ultrabot 的 Pydantic 配置 schema 桥接到 `ProviderManager` 和
`Agent` 所期望的基于字典的接口。我们使用轻量适配器类，而非修改核心组件。

```python
# ultrabot/webui/app.py
"""ultrabot Web 界面的 FastAPI 后端。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pydantic import BaseModel

from ultrabot.agent.agent import Agent
from ultrabot.config.loader import load_config, save_config
from ultrabot.config.schema import Config
from ultrabot.providers.manager import ProviderManager
from ultrabot.security.guard import SecurityConfig as GuardSecurityConfig
from ultrabot.security.guard import SecurityGuard
from ultrabot.session.manager import SessionManager
from ultrabot.tools.base import ToolRegistry
from ultrabot.tools.builtin import register_builtin_tools

_MODULE_DIR = Path(__file__).resolve().parent
_STATIC_DIR = _MODULE_DIR / "static"

# 在启动时填充的全局状态
_config: Config | None = None
_config_path: Path | None = None
_provider_manager: Any = None
_session_manager: SessionManager | None = None
_tool_registry: ToolRegistry | None = None
_security_guard: SecurityGuard | None = None
_agent: Agent | None = None
```

### 步骤 2：配置到组件的适配器

这些适配器至关重要 — 它们让每个子系统看到其期望的配置形状，无需修改
配置 schema 或组件接口。

```python
class _ProviderManagerConfig:
    """将 Pydantic Config 适配为 ProviderManager 期望的基于字典的接口。

    ProviderManager 迭代 config.providers.items()（期望普通字典），
    而 Config.providers 是 Pydantic 模型。此适配器桥接了两者的差异。
    """
    def __init__(self, config: Config) -> None:
        self.providers: dict[str, Any] = {
            name: pcfg for name, pcfg in config.enabled_providers()
        }
        self.default_model: str = config.agents.defaults.model


class _StreamableProviderManager:
    """包装 ProviderManager，为 Agent 暴露 chat_stream_with_retry。

    Agent.run() 调用 self._provider.chat_stream_with_retry(...)，这是
    各个 LLMProvider 实例上的方法。ProviderManager 通过
    chat_with_failover(stream=True) 暴露等效功能。
    """
    def __init__(self, pm: ProviderManager) -> None:
        self._pm = pm

    async def chat_stream_with_retry(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        on_content_delta: Any = None,
        **kwargs: Any,
    ) -> Any:
        return await self._pm.chat_with_failover(
            messages=messages,
            tools=tools,
            on_content_delta=on_content_delta,
            stream=bool(on_content_delta),
            **kwargs,
        )

    def health_check(self) -> dict[str, bool]:
        return self._pm.health_check()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._pm, name)


class _AgentConfig:
    """为 Agent.run() 和系统提示词构建器提供的鸭子类型配置。"""
    def __init__(self, config: Config) -> None:
        defaults = config.agents.defaults
        self.max_tool_iterations: int = defaults.max_tool_iterations
        self.context_window: int = defaults.context_window_tokens
        self.workspace_path: str = str(Path(defaults.workspace).expanduser())
        self.timezone: str = defaults.timezone
        self.model: str = defaults.model
        self.temperature: float = defaults.temperature
        self.max_tokens: int = defaults.max_tokens
        self.reasoning_effort: str = defaults.reasoning_effort
```

### 步骤 3：组件初始化

所有子系统在一个函数中连接，可复用于启动和配置重载。

```python
class ChatRequest(BaseModel):
    message: str
    session_key: str = "web:default"

class ChatResponse(BaseModel):
    response: str


def _redact_api_keys(obj: Any) -> Any:
    """递归地遮蔽键名包含 'key'、'secret' 或 'token' 的值。"""
    if isinstance(obj, dict):
        return {
            k: "***" if isinstance(k, str)
                and any(w in k.lower() for w in ("key", "secret", "token"))
                and isinstance(v, str) and v
                else _redact_api_keys(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact_api_keys(item) for item in obj]
    return obj


def _init_components(config: Config) -> tuple:
    """从配置实例化所有 ultrabot 子系统。"""
    pm = ProviderManager(_ProviderManagerConfig(config))
    provider_manager = _StreamableProviderManager(pm)

    session_manager = SessionManager(
        data_dir=Path.home() / ".ultrabot",
        ttl_seconds=3600,
        max_sessions=1000,
        context_window_tokens=config.agents.defaults.context_window_tokens,
    )

    tool_registry = ToolRegistry()
    agent_config = _AgentConfig(config)
    register_builtin_tools(tool_registry, config=agent_config)

    guard_cfg = GuardSecurityConfig(
        rpm=config.security.rate_limit_rpm,
        burst=config.security.rate_limit_burst,
        max_input_length=config.security.max_input_length,
        blocked_patterns=list(config.security.blocked_patterns),
    )
    security_guard = SecurityGuard(config=guard_cfg)

    agent = Agent(
        config=agent_config,
        provider_manager=provider_manager,
        session_manager=session_manager,
        tool_registry=tool_registry,
        security_guard=None,  # 通道层关注点，非代理层
    )

    return provider_manager, session_manager, tool_registry, security_guard, agent
```

### 步骤 4：FastAPI 应用工厂

```python
def create_app(config_path: str | Path | None = None) -> FastAPI:
    """创建并返回一个完全配置好的 FastAPI 应用。"""
    app = FastAPI(
        title="ultrabot Web UI",
        description="REST API and WebSocket backend for ultrabot.",
        version="0.1.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.config_path = config_path

    @app.on_event("startup")
    async def _startup() -> None:
        global _config, _config_path
        global _provider_manager, _session_manager
        global _tool_registry, _security_guard, _agent

        cfg_path = app.state.config_path
        _config_path = Path(cfg_path).expanduser().resolve() if cfg_path \
            else Path.home() / ".ultrabot" / "config.json"

        logger.info("Loading configuration from {}", _config_path)
        _config = load_config(_config_path)

        (_provider_manager, _session_manager,
         _tool_registry, _security_guard, _agent) = _init_components(_config)
        logger.info("ultrabot web UI backend initialised successfully")

    # --- REST 端点 ---

    @app.get("/api/health")
    async def health_check():
        return {"status": "ok"}

    @app.get("/api/providers")
    async def get_providers():
        if _provider_manager is None:
            raise HTTPException(503, "Server not initialised")
        results = await _provider_manager.validate_providers()
        return {"providers": [
            {"name": n, "healthy": i.get("ok", False), "error": i.get("error"),
             "breaker": i.get("breaker", "closed")}
            for n, i in results.items()
        ]}

    @app.get("/api/sessions")
    async def list_sessions():
        if _session_manager is None:
            raise HTTPException(503, "Server not initialised")
        return {"sessions": await _session_manager.list_sessions()}

    @app.delete("/api/sessions/{session_key:path}")
    async def delete_session(session_key: str):
        if _session_manager is None:
            raise HTTPException(503, "Server not initialised")
        await _session_manager.delete(session_key)
        return {"status": "deleted", "session_key": session_key}

    @app.get("/api/sessions/{session_key:path}/messages")
    async def get_session_messages(session_key: str):
        if _session_manager is None:
            raise HTTPException(503, "Server not initialised")
        session = await _session_manager.get_or_create(session_key)
        return {"session_key": session_key, "messages": session.get_messages()}

    @app.get("/api/tools")
    async def list_tools():
        if _tool_registry is None:
            raise HTTPException(503, "Server not initialised")
        return {"tools": [
            {"name": t.name, "description": t.description, "parameters": t.parameters}
            for t in _tool_registry.list_tools()
        ]}

    @app.get("/api/config")
    async def get_config():
        if _config is None:
            raise HTTPException(503, "Server not initialised")
        raw = _config.model_dump(mode="json", by_alias=True, exclude_none=True)
        return _redact_api_keys(raw)

    @app.post("/api/chat")
    async def chat(body: ChatRequest):
        if _agent is None:
            raise HTTPException(503, "Server not initialised")
        try:
            response = await _agent.run(
                user_message=body.message, session_key=body.session_key,
            )
            return ChatResponse(response=response)
        except Exception as exc:
            raise HTTPException(500, str(exc))

    return app
```

### 步骤 5：WebSocket 流式聊天

WebSocket 端点实时传输内容增量和工具启动通知。

```python
    # 在 create_app 内部，REST 端点之后：

    @app.websocket("/ws/chat")
    async def ws_chat(websocket: WebSocket) -> None:
        """通过 WebSocket 进行实时流式聊天。

        客户端发送：{"type": "message", "content": "Hello!", "session_key": "web:default"}
        服务器发送：{"type": "content_delta", "content": "chunk..."}
                    {"type": "tool_start", "tool_name": "...", "tool_call_id": "..."}
                    {"type": "content_done", "content": "full response"}
                    {"type": "error", "message": "..."}
        """
        await websocket.accept()
        logger.info("WebSocket client connected")

        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.send_json({"type": "error", "message": "Invalid JSON"})
                    continue

                if data.get("type") != "message":
                    await websocket.send_json({
                        "type": "error",
                        "message": f"Unknown message type: {data.get('type')}",
                    })
                    continue

                content = data.get("content", "").strip()
                session_key = data.get("session_key", "web:default")

                if not content or _agent is None:
                    await websocket.send_json({
                        "type": "error", "message": "Empty message or server not ready",
                    })
                    continue

                # 流式回调 — 每条消息使用新的闭包
                async def _on_content_delta(chunk: str) -> None:
                    await websocket.send_json({"type": "content_delta", "content": chunk})

                async def _on_tool_hint(tool_name: str, tool_call_id: str) -> None:
                    await websocket.send_json({
                        "type": "tool_start",
                        "tool_name": tool_name,
                        "tool_call_id": tool_call_id,
                    })

                try:
                    full_response = await _agent.run(
                        user_message=content,
                        session_key=session_key,
                        on_content_delta=_on_content_delta,
                        on_tool_hint=_on_tool_hint,
                    )
                    await websocket.send_json({
                        "type": "content_done", "content": full_response,
                    })
                except Exception as exc:
                    logger.exception("WebSocket chat error for session {}", session_key)
                    await websocket.send_json({"type": "error", "message": str(exc)})

        except WebSocketDisconnect:
            logger.info("WebSocket client disconnected")
```

### 步骤 6：静态文件和服务器启动器

```python
    # 仍在 create_app 内部：
    _STATIC_DIR.mkdir(parents=True, exist_ok=True)

    @app.get("/")
    async def serve_index():
        index_path = _STATIC_DIR / "index.html"
        if not index_path.exists():
            raise HTTPException(404, "index.html not found")
        return FileResponse(index_path)

    # 在 API 路由之后挂载静态文件，确保 /api/* 优先
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    return app


def run_server(host: str = "0.0.0.0", port: int = 8080,
               config_path: str | Path | None = None) -> None:
    """创建应用并在 uvicorn 下启动。"""
    app = create_app(config_path=config_path)
    logger.info("Starting ultrabot web UI on {}:{}", host, port)
    uvicorn.run(app, host=host, port=port)
```

### 测试

```python
# tests/test_webui.py
"""Web 界面 FastAPI 应用的测试。"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from ultrabot.webui.app import _redact_api_keys, create_app


class TestRedactApiKeys:
    def test_redacts_keys(self):
        data = {"api_key": "sk-12345", "name": "test", "nested": {"secret": "abc"}}
        redacted = _redact_api_keys(data)
        assert redacted["api_key"] == "***"
        assert redacted["name"] == "test"
        assert redacted["nested"]["secret"] == "***"

    def test_empty_values_not_redacted(self):
        data = {"api_key": "", "token": None}
        redacted = _redact_api_keys(data)
        assert redacted["api_key"] == ""  # 空字符串不遮蔽

    def test_lists_handled(self):
        data = [{"secret_key": "val"}, {"normal": "ok"}]
        redacted = _redact_api_keys(data)
        assert redacted[0]["secret_key"] == "***"
        assert redacted[1]["normal"] == "ok"


class TestAppFactory:
    def test_create_app_returns_fastapi(self):
        app = create_app(config_path="/nonexistent/config.json")
        assert app.title == "ultrabot Web UI"

    def test_health_endpoint_registered(self):
        app = create_app()
        routes = [r.path for r in app.routes]
        assert "/api/health" in routes

    def test_websocket_endpoint_registered(self):
        app = create_app()
        routes = [r.path for r in app.routes]
        assert "/ws/chat" in routes
```

### 检查点

```bash
# 验证应用能创建并列出其路由
python -c "
from ultrabot.webui.app import create_app
app = create_app()
routes = sorted(set(r.path for r in app.routes if hasattr(r, 'path')))
print('Registered routes:')
for r in routes:
    print(f'  {r}')
"
```

预期输出：
```
Registered routes:
  /
  /api/chat
  /api/config
  /api/health
  /api/providers
  /api/sessions
  /api/sessions/{session_key:path}
  /api/sessions/{session_key:path}/messages
  /api/tools
  /ws/chat
```

### 本课成果

一个完整的 FastAPI Web 后端，包含覆盖每个 ultrabot 子系统（健康检查、提供者、
会话、工具、配置）的 REST 端点，以及一个实时流式传输 LLM 响应的 WebSocket 端点。
适配器类将 Pydantic 配置 schema 桥接到每个组件期望的接口，无需修改核心代码。

---
