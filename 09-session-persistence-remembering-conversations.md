# Ultrabot：30 课程开发指南
**从零开始构建一个生产级 AI 助手框架。**
本指南将带你从"向 LLM 问好"一步步走到一个完整的多提供者、多通道 AI 智能体，具备工具调用、记忆、安全防护和 Web 界面。每节课程都建立在上一节课的基础之上。每节课都包含可运行的代码和测试。  
本教程的主要思路来自于Nanobot(https://github.com/HKUDS/nanobot)以及Learn-Claude-Code(https://github.com/shareAI-lab/learn-claude-code/)，所以对应的叫做Ultrabot。  
本课程设计由AI辅助下完成，因为课程自身也在不停修正，请参考https://github.com/junfhu/UltrabotStepByStep的最新版本，如果您觉得对您有帮助，请帮助点亮一颗星。  
本课程中使用的大模型提供商是火山引擎Code Plan，如果正好你也需要，可以使用我的邀请码获取9折优惠 https://volcengine.com/L/_01BJCkKdMc/  邀请码：HHCDB4J4）  



# 课程 9：会话持久化 — 记住对话

**目标：** 通过将对话会话以 JSON 文件形式持久化到磁盘，为智能体赋予可在重启后存活的记忆。

**你将学到：**
- 使用 `Session` 数据类建模对话
- 在不使用分词器的情况下估算 token 用量
- `datetime` 字段的 JSON 序列化
- 使用 `asyncio.Lock` 实现异步安全的文件 I/O
- 基于 TTL 的清理和 LRU 淘汰
- 上下文窗口修剪（丢弃最旧的消息以控制在 token 预算之内）
- 将会话管理接入智能体的工具循环（`Agent.run()` 的 async 改造）
- 在 CLI 交互模式中接入会话持久化（启动恢复、每轮保存、`/clear` 清磁盘）

**新建文件：**
- `ultrabot/session/__init__.py` — 公共重导出
- `ultrabot/session/manager.py` — `Session` 数据类和 `SessionManager`

**修改文件：**
- `ultrabot/agent.py` — 将 `SessionManager` 接入智能体，`run()` 改为 `async`
- `ultrabot/cli/commands.py` — CLI 交互模式接入 `SessionManager`，实现跨重启记忆

### 步骤 1：Session 数据类

一个 `Session` 就是一次对话。它存储一个有序的消息字典列表
（与 LLM 期望的 `{"role": …, "content": …}` 格式相同）、用于记录的时间戳，
以及一个持续更新的 token 估算值。

创建 `ultrabot/session/manager.py`：

```python
"""会话管理 -- 持久化、TTL 过期和上下文窗口修剪。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger


# ------------------------------------------------------------------
# Session 数据类
# ------------------------------------------------------------------

@dataclass
class Session:
    """单个对话会话。

    Attributes:
        session_id: 唯一标识符（通常为 ``{channel}:{chat_id}``）。
        messages:   发送给/接收自 LLM 的有序消息字典列表。
        created_at: 会话首次创建的 UTC 时间戳。
        last_active: 最近一次活动的 UTC 时间戳。
        metadata:   任意的会话级键值存储。
        token_count: 所有消息的总 token 数量的持续估算值。
    """

    session_id: str
    messages: list[dict] = field(default_factory=list)
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    last_active: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    metadata: dict = field(default_factory=dict)
    token_count: int = 0
```

需要注意两点：
1. 我们对可变默认值使用 `field(default_factory=…)` — 这是数据类的一个经典陷阱。
2. 所有时间戳均为 UTC。永远不要在会话数据中存储本地时间。

### 步骤 2：Token 估算和消息辅助方法

我们需要一种低成本的方式来跟踪会话消耗了多少 token。完整的分词器太重了；
经验法则"约 4 个字符对应 1 个 token"对于修剪决策来说已经足够。

```python
    # -- 在 Session 类内部 --

    @staticmethod
    def _estimate_tokens(content: str) -> int:
        """粗略的 token 估算：约 4 个字符对应 1 个 token。"""
        return max(len(content) // 4, 1)

    def add_message(self, msg: dict) -> None:
        """追加一条消息并更新记录。"""
        self.messages.append(msg)
        content = msg.get("content", "")
        self.token_count += self._estimate_tokens(content)
        self.last_active = datetime.now(timezone.utc)

    def get_messages(self) -> list[dict]:
        """返回消息历史的浅拷贝。"""
        return list(self.messages)

    def clear(self) -> None:
        """清除消息历史并重置 token 计数器。"""
        self.messages.clear()
        self.token_count = 0
        self.last_active = datetime.now(timezone.utc)
```

### 步骤 3：上下文窗口修剪

当会话增长超过 LLM 的上下文窗口时，我们丢弃最旧的非系统消息。
系统提示词是神圣不可侵犯的 — 永远不要修剪它。

```python
    def trim(self, max_tokens: int) -> int:
        """丢弃最旧的非系统消息，直到适应 *max_tokens* 预算。

        返回被移除的消息数量。
        """
        removed = 0
        while self.token_count > max_tokens and self.messages:
            # 永远不要修剪系统提示词（始终在索引 0）。
            if self.messages[0].get("role") == "system":
                if len(self.messages) <= 1:
                    break                        # 只剩系统提示词了
                oldest = self.messages.pop(1)    # 改为移除次旧的消息
            else:
                oldest = self.messages.pop(0)

            tokens = self._estimate_tokens(oldest.get("content", ""))
            self.token_count = max(self.token_count - tokens, 0)
            removed += 1

        if removed:
            logger.debug(
                "Trimmed {} message(s) from session {} (tokens now ~{})",
                removed, self.session_id, self.token_count,
            )
        return removed
```

### 步骤 4：序列化

会话必须能够在进程重启后存活。我们序列化为 JSON，将
`datetime` 对象转换为 ISO-8601 字符串。

```python
    def to_dict(self) -> dict:
        """序列化为适合 JSON 的纯字典。"""
        data = asdict(self)
        data["created_at"] = self.created_at.isoformat()
        data["last_active"] = self.last_active.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict) -> Session:
        """从字典重建一个 Session（例如从磁盘加载）。"""
        data = dict(data)                             # 不修改调用者的数据
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        data["last_active"] = datetime.fromisoformat(data["last_active"])
        return cls(**data)
```

### 步骤 5：SessionManager

`SessionManager` 是负责创建、加载、持久化和
垃圾回收会话的注册中心。它维护一个内存缓存，
以 `~/.ultrabot/sessions/` 下的 JSON 文件作为后端存储。

```python
class SessionManager:
    """拥有、持久化和垃圾回收会话的注册中心。

    Parameters:
        data_dir:  根数据目录。会话保存在 data_dir/sessions/ 下。
        ttl_seconds: 会话空闲多久后有资格被清理。
        max_sessions: 内存中会话数量的上限（LRU 淘汰）。
        context_window_tokens: 每个会话的最大 token 预算。
    """

    def __init__(
        self,
        data_dir: Path,
        ttl_seconds: int = 3600,
        max_sessions: int = 1000,
        context_window_tokens: int = 65536,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self.context_window_tokens = context_window_tokens

        self._sessions_dir = self.data_dir / "sessions"
        self._sessions_dir.mkdir(parents=True, exist_ok=True)

        self._sessions: dict[str, Session] = {}
        self._lock = asyncio.Lock()                   # 守护所有变更操作

        logger.info(
            "SessionManager initialised | data_dir={} ttl={}s max={}",
            self._sessions_dir, ttl_seconds, max_sessions,
        )
```

**为什么使用 `asyncio.Lock`？** 多个通道可能会同时处理不同会话的消息。
锁将对 `_sessions` 的访问序列化，从而避免损坏字典或重复创建会话。

### 步骤 6：核心 CRUD — get、save、load、delete

```python
    def _session_path(self, session_key: str) -> Path:
        """返回 *session_key* 在磁盘上的路径。"""
        safe_name = session_key.replace("/", "_").replace("\\", "_")
        return self._sessions_dir / f"{safe_name}.json"

    async def get_or_create(self, session_key: str) -> Session:
        """获取已有会话或创建新会话。

        1. 检查内存缓存。
        2. 尝试从磁盘加载。
        3. 创建全新的会话。
        """
        async with self._lock:
            if session_key in self._sessions:
                session = self._sessions[session_key]
                session.last_active = datetime.now(timezone.utc)
                return session

            # 尝试从磁盘加载。
            session = await self._load_unlocked(session_key)
            if session is not None:
                self._sessions[session_key] = session
                session.last_active = datetime.now(timezone.utc)
                logger.debug("Session loaded from disk: {}", session_key)
                return session

            # 创建新会话。
            session = Session(session_id=session_key)
            self._sessions[session_key] = session
            logger.info("New session created: {}", session_key)

            # 如果超出上限则淘汰最旧的会话。
            await self._enforce_max_sessions_unlocked()
            return session

    async def save(self, session_key: str) -> None:
        """将会话以 JSON 形式持久化到磁盘。"""
        async with self._lock:
            session = self._sessions.get(session_key)
            if session is None:
                return
            path = self._session_path(session_key)
            data = json.dumps(session.to_dict(), ensure_ascii=False, indent=2)
            path.write_text(data, encoding="utf-8")

    async def _load_unlocked(self, session_key: str) -> Session | None:
        """内部加载器（调用者必须持有 _lock）。"""
        path = self._session_path(session_key)
        if not path.exists():
            return None
        try:
            raw = path.read_text(encoding="utf-8")
            return Session.from_dict(json.loads(raw))
        except Exception:
            logger.exception("Failed to load session from {}", path)
            return None

    async def delete(self, session_key: str) -> None:
        """从内存和磁盘中删除会话。"""
        async with self._lock:
            self._sessions.pop(session_key, None)
            path = self._session_path(session_key)
            if path.exists():
                path.unlink()
```

### 步骤 7：TTL 清理和 LRU 淘汰

```python
    async def cleanup(self) -> int:
        """移除已超过 TTL 的会话。返回移除的数量。"""
        now = datetime.now(timezone.utc)
        removed = 0
        async with self._lock:
            expired = [
                key for key, s in self._sessions.items()
                if (now - s.last_active).total_seconds() > self.ttl_seconds
            ]
            for key in expired:
                del self._sessions[key]
                path = self._session_path(key)
                if path.exists():
                    path.unlink()
                removed += 1
        if removed:
            logger.info("{} expired session(s) cleaned up", removed)
        return removed

    async def _enforce_max_sessions_unlocked(self) -> None:
        """当超过 max_sessions 时淘汰最旧的不活跃会话。
        调用者必须持有 _lock。"""
        while len(self._sessions) > self.max_sessions:
            oldest_key = min(
                self._sessions,
                key=lambda k: self._sessions[k].last_active,
            )
            del self._sessions[oldest_key]
            logger.debug("Evicted oldest session: {}", oldest_key)
```

### 步骤 8：包初始化和接入智能体

创建 `ultrabot/session/__init__.py`：

```python
"""会话管理包的公共 API。"""

from ultrabot.session.manager import Session, SessionManager

__all__ = ["Session", "SessionManager"]
```

现在我们需要将 `SessionManager` 接入 `Agent`，让智能体真正用上会话持久化。
这涉及对 `ultrabot/agent.py` 的四处修改。

#### 8a. 新增 import

在 `agent.py` 顶部添加 `Session` 和 `SessionManager` 的导入：

```python
from ultrabot.session.manager import Session, SessionManager
```

#### 8b. `__init__` — 接受 `sessions` 和 `context_window`

构造函数新增两个可选参数。`self._messages` 保留为没有传入
`SessionManager` 时的回退方案，保证向后兼容：

```python
def __init__(
    self,
    client: OpenAI,
    model: str,
    system_prompt: str = SYSTEM_PROMPT,
    max_iterations: int = 10,
    tool_registry: ToolRegistry | None = None,
    sessions: SessionManager | None = None,        # 新增
    context_window: int = 128_000,                  # 新增
) -> None:
    self._client = client
    self._model = model
    self._system_prompt = system_prompt
    self._max_iterations = max_iterations
    self._tools = tool_registry or ToolRegistry()
    self._sessions = sessions                       # 新增
    self._context_window = context_window            # 新增
    # Fallback for callers that don't use SessionManager.
    self._messages: list[dict[str, Any]] = [
        {"role": "system", "content": self._system_prompt}
    ]
```

#### 8c. `run()` — 改为 `async`，使用 Session 管理消息

`run()` 需要变成 `async def`，因为 `SessionManager.get_or_create()` 和
`.save()` 都是异步方法。新增 `session_key` 参数用于区分不同对话。

整体结构分三段：**获取会话 → 工具循环 → 修剪 + 持久化**。

```python
async def run(
    self,
    user_message: str,
    session_key: str = "default",
    on_content_delta: Callable[[str], None] | None = None,
) -> str:
    # ── 获取/创建会话 ──
    if self._sessions is not None:
        session = await self._sessions.get_or_create(session_key)
        # 首次使用时注入系统提示词。
        if not session.messages:
            session.add_message(
                {"role": "system", "content": self._system_prompt}
            )
        messages = session.messages
    else:
        session = None
        messages = self._messages

    # 1. 追加用户消息（通过 session.add_message 自动更新 token_count）
    if session is not None:
        session.add_message({"role": "user", "content": user_message})
    else:
        messages.append({"role": "user", "content": user_message})

    tool_defs = self._tools.get_definitions() or None

    final_content = ""
    for iteration in range(1, self._max_iterations + 1):
        response = self._chat_stream(tool_defs, on_content_delta, messages)

        # 构建助手消息 ...
        assistant_msg: dict[str, Any] = {"role": "assistant"}
        if response.content:
            assistant_msg["content"] = response.content
        if response.has_tool_calls:
            assistant_msg["tool_calls"] = [...]       # 与之前相同
        if not response.content and not response.has_tool_calls:
            assistant_msg["content"] = ""

        if session is not None:
            session.add_message(assistant_msg)
        else:
            messages.append(assistant_msg)

        if not response.has_tool_calls:
            final_content = response.content or ""
            break

        # 执行工具并追加结果
        for tc in response.tool_calls:
            result = await self._execute_tool(tc)
            tool_msg = {
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            }
            if session is not None:
                session.add_message(tool_msg)
            else:
                messages.append(tool_msg)
    else:
        final_content = "I have reached the maximum number of tool iterations. ..."

    # ── 修剪以保持在上下文窗口内 ──
    if session is not None:
        session.trim(max_tokens=self._context_window)
        await self._sessions.save(session_key)

    return final_content
```

关键改动：
1. **`run()` 变为 `async`** — `SessionManager` 的方法都是异步的，工具执行也不再需要
   `asyncio.run()` 包装，直接 `await self._execute_tool(tc)` 即可。
2. **所有 `messages.append()` 都替换为 `session.add_message()`**（在有 session 时） —
   这确保 `token_count` 实时更新，修剪决策才准确。
3. **`_chat_stream()` 接受显式 `messages` 参数** — 不再默认读取 `self._messages`，
   而是从 session 或回退列表中获取：
   ```python
   def _chat_stream(
       self,
       tools: list[dict] | None,
       on_content_delta: Callable[[str], None] | None = None,
       messages: list[dict[str, Any]] | None = None,
   ) -> LLMResponse:
       kwargs: dict[str, Any] = {
           "model": self._model,
           "messages": messages if messages is not None else self._messages,
           "stream": True,
       }
       # ... 其余不变
   ```
4. **循环结束后先 `trim()` 再 `save()`** — 确保磁盘上存的永远是修剪过的版本，
   不会因为保存了超长会话而下次启动时爆掉上下文窗口。

#### 8d. `clear()` — 支持按 session_key 清除

```python
def clear(self, session_key: str | None = None) -> None:
    """重置对话历史。"""
    if session_key and self._sessions:
        session = self._sessions._sessions.get(session_key)
        if session:
            session.clear()
            session.add_message(
                {"role": "system", "content": self._system_prompt}
            )
    else:
        self._messages = [{"role": "system", "content": self._system_prompt}]
```

### 步骤 9：CLI 交互模式接入会话持久化

`Agent.run()` 已经支持会话了，但 CLI 的交互式 REPL（`ultrabot/cli/commands.py`）
还没有用上。原来的 `_interactive_loop` 每次启动都创建一个新的 `messages` 列表，
退出即丢失 — 这就是"告诉它新名字，重启后就忘了"的根本原因。

#### 9a. `_agent_async` — 创建 SessionManager 并传入

在异步入口点中新增 `SessionManager`，把它连同 `context_window` 一起传给
`_interactive_loop`：

```python
async def _agent_async(cfg_path, message, model):
    from ultrabot.session.manager import SessionManager
    # ... 加载配置、构建 provider、registry（与之前相同）...

    # 构建会话管理器
    sessions = SessionManager(
        data_dir=_DEFAULT_WORKSPACE,           # ~/.ultrabot/
        context_window_tokens=defaults.context_window_tokens,
    )

    # ... 单次模式不变 ...

    # 交互模式 — 把 sessions 传进去
    await _interactive_loop(
        provider, registry, defaults.model,
        sessions=sessions,
        context_window=defaults.context_window_tokens,
    )
```

#### 9b. `_interactive_loop` — 加载/保存/修剪会话

函数签名新增 `sessions` 和 `context_window` 参数。核心改动：

```python
async def _interactive_loop(
    provider, registry, model,
    sessions=None, context_window=128_000,
):
    SYSTEM_MSG = {"role": "system", "content": "You are UltraBot, a helpful assistant."}
    SESSION_KEY = "cli:interactive"

    # ── 启动时：加载或创建会话 ──
    if sessions is not None:
        session = await sessions.get_or_create(SESSION_KEY)
        if not session.messages:
            session.add_message(SYSTEM_MSG)
        messages = session.messages
        if len(messages) > 1:
            console.print(
                f"[dim]Restored {len(messages) - 1} message(s) "
                f"from previous session.[/dim]"
            )
    else:
        session = None
        messages = [SYSTEM_MSG]
```

启动时如果 `~/.ultrabot/sessions/cli:interactive.json` 存在，
`get_or_create` 会从磁盘加载它，用户会看到 "Restored N message(s)" 的提示。

在 REPL 主循环中，每条用户消息和助手回复都通过 `session.add_message()` 写入
（而不是 `messages.append()`），这样 `token_count` 始终准确：

```python
        # -- 普通消息 --
        if session is not None:
            session.add_message({"role": "user", "content": text})
        else:
            messages.append({"role": "user", "content": text})

        # ... 调用 LLM、流式渲染 ...

        # 将助手响应追加到历史记录
        assistant_msg = {"role": "assistant", "content": response.content or full_text}
        if session is not None:
            session.add_message(assistant_msg)
            session.trim(max_tokens=context_window)   # 修剪旧消息
            await sessions.save(SESSION_KEY)           # 持久化到磁盘
        else:
            messages.append(assistant_msg)
```

每轮对话结束后先 `trim()` 再 `save()`，确保磁盘上永远是修剪过的版本。

#### 9c. `/clear` 命令 — 同时清除磁盘

原来的 `/clear` 只重置了内存列表。现在需要同时清除 session 并保存到磁盘，
否则下次启动还会恢复旧对话：

```python
            elif text == "/clear":
                if session is not None:
                    session.clear()
                    session.add_message(SYSTEM_MSG)
                    await sessions.save(SESSION_KEY)
                    messages = session.messages
                else:
                    messages = [SYSTEM_MSG]
                console.print("[dim]Conversation cleared.[/dim]")
```

#### 验证效果

```
$ python -m ultrabot agent
you > My name is Alice
assistant > Nice to meet you, Alice!
you > /quit

$ python -m ultrabot agent
Restored 2 message(s) from previous session.
you > What's my name?
assistant > Your name is Alice!
```

会话文件保存在 `~/.ultrabot/sessions/cli:interactive.json`。

### 测试

```python
# tests/test_session9.py
import asyncio, tempfile
from pathlib import Path
from ultrabot.session.manager import Session, SessionManager


def test_session_add_and_trim():
    s = Session(session_id="test")
    # 添加一个系统提示词 — 它永远不应被修剪。
    s.add_message({"role": "system", "content": "You are helpful."})
    for i in range(20):
        s.add_message({"role": "user", "content": "x" * 400})  # 每条约 100 个 token

    assert s.token_count > 100
    removed = s.trim(max_tokens=200)
    assert removed > 0
    # 系统提示词必须存活。
    assert s.messages[0]["role"] == "system"
    assert s.token_count <= 200


def test_session_serialization():
    s = Session(session_id="round-trip")
    s.add_message({"role": "user", "content": "Hello!"})
    data = s.to_dict()
    restored = Session.from_dict(data)
    assert restored.session_id == "round-trip"
    assert len(restored.messages) == 1


def test_session_manager_persistence():
    async def _run():
        with tempfile.TemporaryDirectory() as tmp:
            mgr = SessionManager(Path(tmp), max_sessions=5)
            session = await mgr.get_or_create("user:42")
            session.add_message({"role": "user", "content": "ping"})
            await mgr.save("user:42")

            # 模拟重启：在同一目录上创建新的 manager。
            mgr2 = SessionManager(Path(tmp))
            reloaded = await mgr2.get_or_create("user:42")
            assert len(reloaded.messages) == 1
            assert reloaded.messages[0]["content"] == "ping"

    asyncio.run(_run())


def test_session_manager_eviction():
    async def _run():
        with tempfile.TemporaryDirectory() as tmp:
            mgr = SessionManager(Path(tmp), max_sessions=2)
            await mgr.get_or_create("a")
            await mgr.get_or_create("b")
            await mgr.get_or_create("c")  # 应该淘汰 "a"
            assert "a" not in mgr._sessions

    asyncio.run(_run())
```

### 检查点

```bash
python -m pytest tests/test_session9.py -v
```

预期结果：全部 4 个测试通过。然后进行实际测试 — 在 CLI REPL 中与智能体对话，退出，
重启，你之前的消息仍然在上下文中。

### 本课成果

一个 `Session` 数据类，跟踪带有 token 估算的对话历史；一个
`SessionManager`，将会话以 JSON 文件形式持久化、通过 TTL 淘汰空闲会话、
通过 LRU 强制执行最大会话数上限，并修剪消息以适应 LLM 的
上下文窗口。`Agent.run()` 已改为 `async`，通过 `session_key` 区分不同对话，
在每轮工具循环结束后自动修剪并持久化会话。CLI 交互模式（`cli/commands.py`）
已接入 `SessionManager`，启动时自动从 `~/.ultrabot/sessions/` 恢复上次对话，
每轮对话后保存到磁盘，`/clear` 命令同时清除内存和磁盘。整个改动向后兼容 —
不传 `sessions` 参数时行为与之前完全一致。对话现在可以在重启后存活。

---
