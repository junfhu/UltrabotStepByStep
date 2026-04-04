# Ultrabot：30 课程开发指南
**从零开始构建一个生产级 AI 助手框架。**
本指南将带你从"向 LLM 问好"一步步走到一个完整的多提供者、多通道 AI 智能体，具备工具调用、记忆、安全防护和 Web 界面。每节课程都建立在上一节课的基础之上。每节课都包含可运行的代码和测试。  
本教程的主要思路来自于
- Nanobot (https://github.com/HKUDS/nanobot)
- Learn-Claude-Code (https://github.com/shareAI-lab/learn-claude-code/)

本课程设计由AI辅助下完成，因为课程自身也在不停修正，请参考 https://github.com/junfhu/UltrabotStepByStep，如果您觉得对您有帮助，请帮助点亮一颗星。  
本课程中使用的大模型提供商是火山引擎Code Plan，如果正好你也需要，可以使用我的邀请码获取9折优惠 https://volcengine.com/L/_01BJCkKdMc/  邀请码：HHCDB4J4）  



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

## 本课使用的 Python 知识

### FastAPI 框架与路由装饰器

FastAPI 是一个高性能的 Python Web 框架，基于类型提示自动生成文档。路由装饰器（如 `@app.get()`、`@app.post()`）将 URL 路径映射到处理函数。

```python
from fastapi import FastAPI

app = FastAPI()

@app.get("/hello")
async def hello():
    return {"message": "你好世界"}

@app.post("/items")
async def create_item(name: str):
    return {"name": name}
```

**为什么在本课中使用：** ultrabot 的 Web 界面需要提供多个 REST API（健康检查、提供者状态、会话管理、工具列表等）。FastAPI 的装饰器语法让每个端点的定义简洁明了，且自动生成 OpenAPI 文档。

### `pydantic.BaseModel`（数据验证模型）

Pydantic 的 `BaseModel` 自动对输入数据进行类型验证和转换。FastAPI 使用它来验证请求体和生成 API 文档。

```python
from pydantic import BaseModel

class ChatRequest(BaseModel):
    message: str
    session_key: str = "web:default"  # 带默认值

# FastAPI 自动验证 JSON 请求体
@app.post("/chat")
async def chat(body: ChatRequest):
    return {"echo": body.message}
```

**为什么在本课中使用：** `ChatRequest` 和 `ChatResponse` 模型确保客户端发送的数据格式正确（如 `message` 必须是字符串）。如果格式不对，FastAPI 会自动返回 422 错误，无需手动校验。

### WebSocket（实时双向通信）

WebSocket 是一种全双工通信协议，服务器和客户端可以随时互相发送消息，不像 HTTP 需要"请求-响应"配对。FastAPI 原生支持 WebSocket。

```python
from fastapi import WebSocket, WebSocketDisconnect

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            data = await ws.receive_text()  # 接收客户端消息
            await ws.send_json({"echo": data})  # 发送给客户端
    except WebSocketDisconnect:
        print("客户端断开连接")
```

**为什么在本课中使用：** 聊天界面需要实时流式显示 LLM 的回复（一个字一个字地出现）。WebSocket 让服务器可以在生成过程中不断推送内容增量（`content_delta`），实现打字机效果。

### 应用工厂模式（`create_app()`）

工厂函数封装了应用的创建和配置过程，返回一个完全配置好的应用实例。这种模式方便测试（可以创建不同配置的应用）和延迟初始化。

```python
def create_app(config_path=None):
    app = FastAPI(title="My App")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app

# 生产环境
app = create_app("/etc/myapp/config.json")

# 测试环境
test_app = create_app("/tmp/test_config.json")
```

**为什么在本课中使用：** `create_app()` 将 FastAPI 应用的创建、中间件配置、路由注册和生命周期管理封装在一个函数中。测试代码可以直接调用 `create_app()` 获取应用实例进行路由检查，无需启动服务器。

### 适配器模式与鸭子类型

适配器模式将一个接口转换为另一个接口。Python 的鸭子类型（"如果它像鸭子一样走路和叫，那它就是鸭子"）让适配器只需实现目标接口的方法，无需显式继承。

```python
class LegacyPrinter:
    def print_text(self, text):
        print(text)

class ModernPrinterAdapter:
    """适配旧接口到新接口"""
    def __init__(self, legacy):
        self._legacy = legacy

    def output(self, text):  # 新接口期望的方法名
        self._legacy.print_text(text)
```

**为什么在本课中使用：** `_ProviderManagerConfig` 和 `_StreamableProviderManager` 是适配器类。Pydantic 配置模型与 `ProviderManager` 期望的字典接口不匹配，适配器在两者之间"翻译"，无需修改任何一方的代码。

### `__getattr__()`（属性代理）

当访问对象上不存在的属性时，Python 会调用 `__getattr__()` 方法。它常用于实现代理模式——将未知属性的访问转发给被包装的对象。

```python
class Proxy:
    def __init__(self, target):
        self._target = target

    def __getattr__(self, name):
        return getattr(self._target, name)  # 转发到目标对象

import json
proxy = Proxy(json)
data = proxy.loads('{"a": 1}')  # 实际调用 json.loads
```

**为什么在本课中使用：** `_StreamableProviderManager` 包装了 `ProviderManager`，增加了 `chat_stream_with_retry` 方法。对于其他方法（如 `health_check`），通过 `__getattr__` 自动转发到原始 `ProviderManager`，无需逐个复制。

### `global` 关键字（全局变量）

`global` 关键字声明函数内的变量是全局变量，而非局部变量。这让函数可以修改模块级别的变量。

```python
_counter = 0

def increment():
    global _counter  # 声明要修改全局变量
    _counter += 1

increment()
print(_counter)  # 1
```

**为什么在本课中使用：** `_startup()` 事件处理器需要初始化模块级别的 `_config`、`_agent` 等全局变量，让后续的路由处理函数可以访问。在 FastAPI 的生命周期回调中，`global` 是设置共享状态的简单方式。

### 递归函数（`_redact_api_keys`）

递归函数是调用自身的函数，适合处理嵌套数据结构（如字典中嵌套字典、列表中嵌套字典等）。每次递归处理一层嵌套。

```python
def deep_process(obj):
    if isinstance(obj, dict):
        return {k: deep_process(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [deep_process(item) for item in obj]
    return obj  # 基本类型直接返回（递归终止条件）
```

**为什么在本课中使用：** 配置对象可能有多层嵌套（提供者配置中嵌套 API 密钥），`_redact_api_keys()` 递归遍历所有层级，将名称包含 `key`、`secret`、`token` 的字段值替换为 `"***"`，防止敏感信息泄露。

### `isinstance()` 类型检查

`isinstance(obj, type)` 检查对象是否是指定类型的实例。比 `type(obj) == type` 更好，因为它也支持子类检查。

```python
value = [1, 2, 3]

if isinstance(value, list):
    print("是列表")
elif isinstance(value, dict):
    print("是字典")
elif isinstance(value, str):
    print("是字符串")
```

**为什么在本课中使用：** `_redact_api_keys()` 需要判断当前处理的节点类型——字典需要检查键名、列表需要递归每个元素、其他类型直接返回。`isinstance` 让这种类型分支清晰安全。

### `any()` 内置函数

`any()` 接受一个可迭代对象，只要其中任何一个元素为真就返回 `True`。常用于检查是否存在至少一个满足条件的元素。

```python
words = ["key", "secret", "token"]
field_name = "api_key"

if any(w in field_name.lower() for w in words):
    print("这是敏感字段！")
```

**为什么在本课中使用：** 判断字典键名是否包含敏感词（`key`、`secret`、`token`），`any(w in k.lower() for w in ("key", "secret", "token"))` 简洁地表达了"只要命中任意一个敏感词就遮蔽"的逻辑。

### 字典推导式

字典推导式可以在一行中创建或转换字典，语法为 `{key_expr: value_expr for item in iterable}`。

```python
original = {"a": 1, "b": 2, "c": 3}
doubled = {k: v * 2 for k, v in original.items()}
print(doubled)  # {'a': 2, 'b': 4, 'c': 6}
```

**为什么在本课中使用：** `_redact_api_keys()` 用字典推导式遍历配置字典的每个键值对，对敏感键进行遮蔽处理，一行代码完成整个字典的转换。`_ProviderManagerConfig` 也用字典推导式构建提供者配置。

### 闭包（Closure）

闭包是指内部函数"记住"了外部函数作用域中变量的函数。即使外部函数已返回，内部函数仍能访问这些变量。

```python
def make_greeter(name):
    def greet():
        print(f"Hello, {name}!")  # 引用外层变量 name
    return greet

say_hi = make_greeter("Alice")
say_hi()  # Hello, Alice!
```

**为什么在本课中使用：** WebSocket 处理循环中，每条消息创建新的 `_on_content_delta` 和 `_on_tool_hint` 闭包函数。这些闭包"捕获"了当前的 `websocket` 对象，使回调函数能够将内容增量发送到正确的 WebSocket 连接。

### `@app.on_event("startup")`（生命周期事件）

FastAPI 的生命周期事件让你在应用启动或关闭时执行初始化/清理代码。`startup` 事件在第一个请求之前触发。

```python
@app.on_event("startup")
async def on_startup():
    print("应用启动，初始化数据库连接...")
    # 连接数据库、加载配置等

@app.on_event("shutdown")
async def on_shutdown():
    print("应用关闭，清理资源...")
```

**为什么在本课中使用：** `_startup()` 在服务器启动时加载配置文件、初始化所有子系统（提供者管理器、会话管理器、工具注册表、安全守卫、代理）。确保所有组件在接受请求前已就绪。

### `CORSMiddleware`（跨域资源共享中间件）

CORS 中间件处理浏览器的跨域安全限制。没有它，从不同域名/端口的前端页面无法调用 API。

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],       # 允许所有来源
    allow_methods=["*"],       # 允许所有 HTTP 方法
    allow_headers=["*"],       # 允许所有请求头
)
```

**为什么在本课中使用：** Web 前端（`index.html`）可能从不同端口或域名访问 API。`CORSMiddleware` 配置为 `allow_origins=["*"]` 允许来自任何来源的请求，方便开发和调试。

### `Path(__file__).resolve().parent`（模块路径定位）

`__file__` 是当前 Python 文件的路径。配合 `Path` 的 `resolve()`（转为绝对路径）和 `parent`（获取父目录），可以定位相对于模块的文件。

```python
from pathlib import Path

_MODULE_DIR = Path(__file__).resolve().parent
_STATIC_DIR = _MODULE_DIR / "static"
_TEMPLATE_DIR = _MODULE_DIR / "templates"
```

**为什么在本课中使用：** 静态文件（`index.html`、CSS、JS）存放在 `ultrabot/webui/static/` 目录中。通过 `Path(__file__).resolve().parent / "static"` 定位静态文件目录，无论从哪里启动应用都能正确找到。

### `uvicorn`（ASGI 服务器）

`uvicorn` 是一个高性能的 ASGI 服务器，用于运行 FastAPI 等异步 Web 应用。`uvicorn.run()` 是最简单的启动方式。

```python
import uvicorn

app = create_app()
uvicorn.run(app, host="0.0.0.0", port=8080)
```

**为什么在本课中使用：** `run_server()` 用 `uvicorn.run()` 启动 FastAPI 应用，监听指定的主机和端口。uvicorn 支持异步处理和 WebSocket，是 FastAPI 的标准搭配。

### `unittest.mock`（测试模拟）

`unittest.mock` 提供 `MagicMock` 和 `AsyncMock` 等工具，用于在测试中替换真实的依赖。`patch` 可以临时替换模块中的对象。

```python
from unittest.mock import AsyncMock, MagicMock

mock_agent = AsyncMock()
mock_agent.run.return_value = "模拟回复"

result = await mock_agent.run(user_message="test")
print(result)  # "模拟回复"
```

**为什么在本课中使用：** 测试 Web 界面时不需要真正连接 LLM 服务。`AsyncMock` 可以模拟 `Agent.run()` 的返回值，让测试只验证 API 路由和数据处理逻辑，不依赖外部服务。
