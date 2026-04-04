# Ultrabot：30 课程开发指南
**从零开始构建一个生产级 AI 助手框架。**
本指南将带你从"向 LLM 问好"一步步走到一个完整的多提供者、多通道 AI 智能体，具备工具调用、记忆、安全防护和 Web 界面。每节课程都建立在上一节课的基础之上。每节课都包含可运行的代码和测试。  
本教程的主要思路来自于
- Nanobot (https://github.com/HKUDS/nanobot)
- Learn-Claude-Code (https://github.com/shareAI-lab/learn-claude-code/)

本课程设计由AI辅助下完成，因为课程自身也在不停修正，请参考 https://github.com/junfhu/UltrabotStepByStep，如果您觉得对您有帮助，请帮助点亮一颗星。  
本课程中使用的大模型提供商是火山引擎Code Plan，如果正好你也需要，可以使用我的邀请码获取9折优惠 https://volcengine.com/L/_01BJCkKdMc/  邀请码：HHCDB4J4）  



# 课程 18：专家路由器 + 动态切换

**目标：** 构建一个智能消息路由器，将用户消息定向到正确的专家人设，支持显式命令、粘性会话和基于 LLM 的自动路由。

**你将学到：**
- 用于路由结果的 `RouteResult` dataclass
- 命令解析：`@slug`、`/expert slug`、`/expert off`、`/experts`
- 按聊天会话的粘性会话跟踪
- 使用专家目录的基于 LLM 的自动路由
- 从 GitHub 同步下载人设文件

**新建文件：**
- `ultrabot/experts/router.py` — 消息到专家的路由引擎
- `ultrabot/experts/sync.py` — 从 GitHub 下载人设

### 步骤 1：RouteResult Dataclass

每个路由决策都会产生一个 `RouteResult`，告诉代理使用哪个人设以及决策是如何做出的。

```python
# ultrabot/experts/router.py
"""专家路由器 -- 为每条入站消息选择合适的专家。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from ultrabot.experts.parser import ExpertPersona
    from ultrabot.experts.registry import ExpertRegistry


@dataclass(slots=True)
class RouteResult:
    """将用户消息路由到专家的结果。

    属性：
        persona: 选中的 ExpertPersona，或 None 表示使用默认代理。
        cleaned_message: 去除路由命令后的用户消息。
        source: 选择方式："command"、"sticky"、"auto" 或 "default"。
    """
    persona: ExpertPersona | None
    cleaned_message: str
    source: str = "default"
```

### 步骤 2：命令模式匹配

路由器识别四种命令模式。正则表达式模式同时处理 `@slug` 和 `/expert slug` 语法。

```python
# @slug ...  或  /expert slug ...
_AT_PATTERN = re.compile(r"^@([\w-]+)\s*", re.UNICODE)
_SLASH_PATTERN = re.compile(
    r"^/expert\s+([\w-]+)\s*", re.UNICODE | re.IGNORECASE
)
# /expert off  或  @default
_OFF_PATTERNS = re.compile(
    r"^(?:/expert\s+off|@default)\b\s*", re.UNICODE | re.IGNORECASE
)
# /experts（列出全部）或  /experts query（搜索）
_LIST_PATTERN = re.compile(
    r"^/experts(?:\s+(.+))?\s*$", re.UNICODE | re.IGNORECASE
)
```

### 步骤 3：ExpertRouter

路由器实现了清晰的优先级链：
1. 停用命令（`/expert off`）
2. 列表命令（`/experts`）
3. 显式命令（`@slug` 或 `/expert slug`）
4. 粘性会话（之前选择的专家持续生效）
5. LLM 自动路由（如果启用）
6. 默认代理

```python
class ExpertRouter:
    """将入站消息路由到专家人设。

    参数：
        registry: 包含已加载人设的 ExpertRegistry。
        auto_route: 是否使用基于 LLM 的自动路由。
        provider_manager: 可选的 ProviderManager，用于自动路由。
    """

    def __init__(
        self,
        registry: "ExpertRegistry",
        auto_route: bool = False,
        provider_manager: Any | None = None,
    ) -> None:
        self._registry = registry
        self._auto_route = auto_route
        self._provider = provider_manager
        # 会话-slug 粘性映射：session_key -> 专家 slug
        self._sticky: dict[str, str] = {}

    async def route(
        self,
        message: str,
        session_key: str,
    ) -> RouteResult:
        """确定哪个专家应处理 *message*。"""
        # 1. 停用命令
        m = _OFF_PATTERNS.match(message)
        if m:
            self._sticky.pop(session_key, None)
            cleaned = message[m.end():].strip() or "OK, switched back to default mode."
            return RouteResult(persona=None, cleaned_message=cleaned, source="command")

        # 2. 列表命令
        m = _LIST_PATTERN.match(message)
        if m:
            query = (m.group(1) or "").strip()
            listing = self._build_listing(query)
            return RouteResult(persona=None, cleaned_message=listing, source="command")

        # 3. 显式专家命令
        slug, cleaned = self._extract_command(message)
        if slug:
            persona = self._resolve_slug(slug)
            if persona:
                self._sticky[session_key] = persona.slug
                logger.info("Routed session {!r} to expert {!r} (command)",
                            session_key, persona.slug)
                return RouteResult(persona=persona, cleaned_message=cleaned,
                                   source="command")
            logger.warning("Unknown expert slug: {!r}", slug)

        # 4. 粘性会话
        sticky_slug = self._sticky.get(session_key)
        if sticky_slug:
            persona = self._registry.get(sticky_slug)
            if persona:
                return RouteResult(persona=persona, cleaned_message=message,
                                   source="sticky")
            del self._sticky[sticky_slug]  # 已过期 — 清理

        # 5. 自动路由（基于 LLM）
        if self._auto_route and self._provider and len(self._registry) > 0:
            persona = await self._auto_select(message)
            if persona:
                self._sticky[session_key] = persona.slug
                logger.info("Auto-routed session {!r} to expert {!r}",
                            session_key, persona.slug)
                return RouteResult(persona=persona, cleaned_message=message,
                                   source="auto")

        # 6. 默认
        return RouteResult(persona=None, cleaned_message=message, source="default")

    def clear_sticky(self, session_key: str) -> None:
        self._sticky.pop(session_key, None)

    def get_sticky(self, session_key: str) -> str | None:
        return self._sticky.get(session_key)
```

### 步骤 4：内部路由辅助方法

```python
    # -- 内部方法（仍在 ExpertRouter 内）--

    def _extract_command(self, message: str) -> tuple[str | None, str]:
        """尝试从消息中提取显式专家命令。"""
        m = _AT_PATTERN.match(message)
        if m:
            return m.group(1), message[m.end():].strip() or message

        m = _SLASH_PATTERN.match(message)
        if m:
            return m.group(1), message[m.end():].strip() or message

        return None, message

    def _resolve_slug(self, slug: str) -> "ExpertPersona | None":
        """在注册表中查找 slug，先精确匹配再按名称匹配。"""
        persona = self._registry.get(slug)
        if persona:
            return persona
        return self._registry.get_by_name(slug)

    def _build_listing(self, query: str) -> str:
        """构建格式化的专家列表，可选过滤。"""
        if query:
            results = self._registry.search(query, limit=20)
            if not results:
                return f"No experts found for '{query}'."
            lines = [f"**Experts matching '{query}':**\n"]
            for p in results:
                lines.append(f"- `@{p.slug}` -- {p.name}: {p.description[:60]}")
            return "\n".join(lines)

        departments = self._registry.departments()
        if not departments:
            return "No experts loaded. Run `ultrabot experts sync` to download."

        lines = [f"**{len(self._registry)} experts across {len(departments)} departments:**\n"]
        for dept in departments:
            experts = self._registry.list_department(dept)
            names = ", ".join(f"`{p.slug}`" for p in experts[:5])
            suffix = f" ... +{len(experts) - 5} more" if len(experts) > 5 else ""
            lines.append(f"- **{dept}** ({len(experts)}): {names}{suffix}")
        lines.append("\nUse `@slug` to activate an expert, `/experts query` to search.")
        return "\n".join(lines)

    async def _auto_select(self, message: str) -> "ExpertPersona | None":
        """使用 LLM 调用为消息选择最佳专家。"""
        catalog = self._registry.build_catalog()

        system = (
            "You are an expert routing assistant. Given the user's message, "
            "pick the single best expert from the catalog below. "
            "Return ONLY the expert slug (e.g. 'engineering-frontend-developer') "
            "or 'none' if no expert is a good match.\n\n"
            f"EXPERT CATALOG:\n{catalog}"
        )

        try:
            response = await self._provider.chat_with_failover(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": message},
                ],
                max_tokens=60,
                temperature=0.0,
            )
            slug = (response.content or "").strip().lower().strip("`'\"")
            if slug and slug != "none":
                return self._registry.get(slug)
        except Exception:
            logger.exception("Auto-route LLM call failed")

        return None
```

### 步骤 5：从 GitHub 同步人设

```python
# ultrabot/experts/sync.py
"""从 agency-agents-zh GitHub 仓库同步专家人设。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from loguru import logger

REPO_OWNER = "jnMetaCode"
REPO_NAME = "agency-agents-zh"
BRANCH = "main"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/{BRANCH}"
API_TREE = (
    f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}"
    f"/git/trees/{BRANCH}?recursive=1"
)

PERSONA_DIRS = frozenset({
    "academic", "design", "engineering", "finance", "game-development",
    "hr", "integrations", "legal", "marketing", "paid-media", "product",
    "project-management", "sales", "spatial-computing", "specialized",
    "supply-chain", "support", "testing",
})


def sync_personas(
    dest_dir: Path,
    *,
    departments: set[str] | None = None,
    force: bool = False,
    progress_callback: Any = None,
) -> int:
    """从 GitHub 下载人设 ``.md`` 文件到 *dest_dir*。

    返回下载的文件数量。
    """
    dest_dir.mkdir(parents=True, exist_ok=True)

    # 1. 获取仓库树
    logger.info("Fetching repository tree from GitHub ...")
    try:
        tree = _fetch_tree()
    except Exception as exc:
        raise RuntimeError(f"Cannot reach GitHub API: {exc}") from exc

    # 2. 过滤出人设 .md 文件
    files = _filter_persona_files(tree, departments)
    total = len(files)
    logger.info("Found {} persona files to sync", total)

    if total == 0:
        return 0

    # 3. 下载每个文件
    downloaded = 0
    for idx, file_path in enumerate(files, 1):
        filename = Path(file_path).name
        local_path = dest_dir / filename

        if local_path.exists() and not force:
            if progress_callback:
                progress_callback(idx, total, filename)
            continue

        try:
            content = _fetch_raw_file(file_path)
            local_path.write_text(content, encoding="utf-8")
            downloaded += 1
        except Exception:
            logger.exception("Failed to download {}", file_path)

        if progress_callback:
            progress_callback(idx, total, filename)

    logger.info("Synced {}/{} persona files to {}", downloaded, total, dest_dir)
    return downloaded


async def async_sync_personas(dest_dir: Path, **kwargs: Any) -> int:
    """sync_personas 的异步包装器（在执行器中运行）。"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: sync_personas(dest_dir, **kwargs))


def _fetch_tree() -> list[dict[str, Any]]:
    req = Request(API_TREE, headers={"Accept": "application/json"})
    with urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("tree", [])


def _filter_persona_files(tree: list[dict[str, Any]], departments: set[str] | None) -> list[str]:
    files: list[str] = []
    for item in tree:
        if item.get("type") != "blob":
            continue
        path = item.get("path", "")
        if not path.endswith(".md"):
            continue
        parts = path.split("/")
        if len(parts) != 2:
            continue
        dept, filename = parts
        if dept not in PERSONA_DIRS:
            continue
        if departments and dept not in departments:
            continue
        if filename.startswith("_") or filename.upper() == "README.MD":
            continue
        files.append(path)
    return sorted(files)


def _fetch_raw_file(path: str) -> str:
    url = f"{RAW_BASE}/{path}"
    with urlopen(Request(url), timeout=15) as resp:
        return resp.read().decode("utf-8")
```

### 测试

```python
# tests/test_experts_router.py
"""专家路由器和同步模块的测试。"""

import pytest

from ultrabot.experts.parser import parse_persona_text
from ultrabot.experts.registry import ExpertRegistry
from ultrabot.experts.router import ExpertRouter, RouteResult


CODER_MD = """\
---
name: "Coder"
description: "Expert Python programmer"
---
## Your identity
You write Python code.
"""

WRITER_MD = """\
---
name: "Writer"
description: "Creative content writer"
---
## Your identity
You write compelling content.
"""


@pytest.fixture
def registry():
    reg = ExpertRegistry()
    reg.register(parse_persona_text(CODER_MD, slug="coder"))
    reg.register(parse_persona_text(WRITER_MD, slug="writer"))
    return reg


@pytest.fixture
def router(registry):
    return ExpertRouter(registry, auto_route=False)


class TestCommandRouting:
    @pytest.mark.asyncio
    async def test_at_command(self, router):
        result = await router.route("@coder Fix this bug", session_key="s1")
        assert result.source == "command"
        assert result.persona is not None
        assert result.persona.slug == "coder"
        assert result.cleaned_message == "Fix this bug"

    @pytest.mark.asyncio
    async def test_slash_command(self, router):
        result = await router.route("/expert writer Draft an email", session_key="s1")
        assert result.persona.slug == "writer"
        assert result.cleaned_message == "Draft an email"

    @pytest.mark.asyncio
    async def test_expert_off(self, router):
        # 先激活一个专家
        await router.route("@coder hello", session_key="s1")
        assert router.get_sticky("s1") == "coder"

        # 然后停用
        result = await router.route("/expert off", session_key="s1")
        assert result.persona is None
        assert result.source == "command"
        assert router.get_sticky("s1") is None

    @pytest.mark.asyncio
    async def test_unknown_slug_falls_through(self, router):
        result = await router.route("@nonexistent hello", session_key="s1")
        assert result.source == "default"
        assert result.persona is None


class TestStickySession:
    @pytest.mark.asyncio
    async def test_sticky_persists(self, router):
        await router.route("@coder hello", session_key="s1")
        # 不带命令的下一条消息应继续使用 coder
        result = await router.route("What about this?", session_key="s1")
        assert result.source == "sticky"
        assert result.persona.slug == "coder"

    @pytest.mark.asyncio
    async def test_different_sessions_independent(self, router):
        await router.route("@coder hello", session_key="s1")
        result = await router.route("Hello", session_key="s2")
        assert result.source == "default"  # s2 没有粘性会话


class TestListCommand:
    @pytest.mark.asyncio
    async def test_list_all(self, router):
        result = await router.route("/experts", session_key="s1")
        assert result.source == "command"
        assert "2 experts" in result.cleaned_message

    @pytest.mark.asyncio
    async def test_list_search(self, router):
        result = await router.route("/experts Python", session_key="s1")
        assert "coder" in result.cleaned_message.lower()
```

### 检查点

```bash
python -c "
import asyncio
from ultrabot.experts.parser import parse_persona_text
from ultrabot.experts.registry import ExpertRegistry
from ultrabot.experts.router import ExpertRouter

reg = ExpertRegistry()
reg.register(parse_persona_text('---\nname: Coder\n---\n## Your identity\nPython expert.', slug='coder'))
reg.register(parse_persona_text('---\nname: Writer\n---\n## Your identity\nCreative writer.', slug='writer'))

router = ExpertRouter(reg)

async def demo():
    r = await router.route('@coder Fix the tests', 's1')
    print(f'1) source={r.source}, expert={r.persona.slug}, msg={r.cleaned_message!r}')

    r = await router.route('What about imports?', 's1')
    print(f'2) source={r.source}, expert={r.persona.slug} (sticky!)')

    r = await router.route('/expert off', 's1')
    print(f'3) source={r.source}, expert={r.persona} (back to default)')

    r = await router.route('/experts', 's1')
    print(f'4) Listing: {r.cleaned_message[:80]}...')

asyncio.run(demo())
"
```

预期输出：
```
1) source=command, expert=coder, msg='Fix the tests'
2) source=sticky, expert=coder (sticky!)
3) source=command, expert=None (back to default)
4) Listing: **2 experts across 1 departments:**
...
```

### 本课成果

一个具备三种路由策略的专家路由器：显式命令（`@slug`、`/expert slug`）、
跨消息持续保持活跃专家的粘性会话，以及从目录中选择最佳专家的基于 LLM 的
自动路由。此外还有一个 GitHub 同步模块，可下载完整的人设语料库。

---

## 本课使用的 Python 知识

### `@dataclass(slots=True)`（带 slots 的数据类）

`@dataclass` 自动生成构造函数和常用方法。`slots=True` 让实例使用固定的属性槽而非字典，节省内存且访问更快。

```python
from dataclasses import dataclass

@dataclass(slots=True)
class Point:
    x: float
    y: float

p = Point(1.0, 2.0)
print(p)  # Point(x=1.0, y=2.0)
```

**为什么在本课中使用：** `RouteResult` 是每次路由决策的返回值，包含被选中的专家、清理后的消息和决策来源。`@dataclass` 让这个结构的定义简洁明了，`slots=True` 确保频繁创建的路由结果对象内存高效。

### `re.compile()` 与正则表达式标志组合

`re.compile()` 预编译正则表达式模式。多个标志可以用 `|`（按位或）组合：`re.UNICODE` 支持 Unicode 字符匹配，`re.IGNORECASE` 不区分大小写。

```python
import re

pattern = re.compile(r"^/command\s+(\w+)", re.UNICODE | re.IGNORECASE)
m = pattern.match("/Command Hello")
if m:
    print(m.group(1))  # "Hello"
```

**为什么在本课中使用：** 路由器需要解析多种命令格式（`@slug`、`/expert slug`、`/expert off`、`/experts`），每种都有独立的正则模式。`re.UNICODE` 确保中文 slug 也能匹配，`re.IGNORECASE` 让 `/Expert` 和 `/expert` 都有效。

### `async def` / `await`（异步方法）

在类中定义 `async def` 方法，使其成为异步方法。调用时需要 `await`，适合包含网络调用或其他 I/O 操作的方法。

```python
class DataFetcher:
    async def fetch(self, url: str) -> str:
        # 模拟异步网络请求
        await asyncio.sleep(1)
        return "data"

fetcher = DataFetcher()
result = await fetcher.fetch("https://example.com")
```

**为什么在本课中使用：** `ExpertRouter.route()` 是异步方法，因为自动路由模式需要调用 LLM API（`_auto_select` 通过 `chat_with_failover` 请求 LLM），这是一个网络 I/O 操作，必须用 `await` 等待。

### `dict` 作为缓存（粘性会话映射）

Python 字典可以作为简单的内存缓存使用。键是查找标识符，值是缓存的数据。

```python
sticky_sessions = {}

# 记住用户的选择
sticky_sessions["user:123"] = "coder"

# 下次查找
expert = sticky_sessions.get("user:123")  # "coder"
print(expert)
```

**为什么在本课中使用：** `ExpertRouter` 用 `self._sticky: dict[str, str]` 存储每个会话当前激活的专家 slug。用户选择专家后，后续消息会自动路由到同一专家，直到明确切换——这就是"粘性会话"机制。

### 正则匹配对象（`match.group()` 与 `match.end()`）

`re.match()` 返回一个 `Match` 对象。`.group(1)` 获取第一个捕获组的内容，`.end()` 获取匹配结束的位置，方便截取剩余文本。

```python
import re

m = re.match(r"^@(\w+)\s*", "@coder Fix this bug")
if m:
    slug = m.group(1)           # "coder"
    rest = "@coder Fix this bug"[m.end():]  # "Fix this bug"
```

**为什么在本课中使用：** 命令解析需要同时提取 slug（如 `coder`）和剩余消息文本（如 `Fix this bug`）。`m.group(1)` 取出 slug，`message[m.end():]` 截取命令之后的用户实际消息。

### 条件链优先级模式

通过一系列 `if/elif` 或连续的 `if ... return` 实现优先级从高到低的匹配逻辑。先检查的条件优先级更高。

```python
async def route(self, message, session_key):
    # 优先级 1：停用命令
    if is_off_command(message):
        return handle_off()

    # 优先级 2：列表命令
    if is_list_command(message):
        return handle_list()

    # 优先级 3：显式命令
    # 优先级 4：粘性会话
    # 优先级 5：自动路由
    # 优先级 6：默认
    return default_route()
```

**为什么在本课中使用：** `ExpertRouter.route()` 按 6 级优先级处理消息：停用命令 > 列表命令 > 显式命令 > 粘性会话 > LLM 自动路由 > 默认。每级用 `if + return` 实现"命中即返回"，清晰表达优先级链。

### `urllib.request`（标准库 HTTP 请求）

`urllib.request` 是 Python 标准库的 HTTP 模块，无需安装第三方库即可发起网络请求。`Request` 对象可设置请求头，`urlopen` 执行请求。

```python
from urllib.request import Request, urlopen
import json

req = Request("https://api.github.com/repos/user/repo",
              headers={"Accept": "application/json"})
with urlopen(req, timeout=30) as resp:
    data = json.loads(resp.read().decode("utf-8"))
```

**为什么在本课中使用：** `sync.py` 从 GitHub API 下载人设文件。使用标准库而非 `requests` 或 `httpx`，避免了额外的依赖安装——同步功能是辅助工具，不值得引入新依赖。

### `json.loads()` / `json.dumps()`（JSON 序列化）

`json.loads()` 将 JSON 字符串解析为 Python 对象（字典/列表），`json.dumps()` 将 Python 对象序列化为 JSON 字符串。

```python
import json

data = {"name": "Alice", "age": 30}
json_str = json.dumps(data, indent=2)  # Python -> JSON 字符串
parsed = json.loads(json_str)           # JSON 字符串 -> Python
print(parsed["name"])  # "Alice"
```

**为什么在本课中使用：** GitHub API 返回 JSON 格式的仓库文件树。`_fetch_tree()` 用 `json.loads()` 解析 API 响应，提取人设文件的路径列表。

### `frozenset`（不可变常量集合）

`frozenset` 创建后不能修改，适合存储固定的配置值或白名单。查找操作的时间复杂度为 O(1)。

```python
ALLOWED_DEPTS = frozenset({"engineering", "design", "marketing"})

dept = "engineering"
if dept in ALLOWED_DEPTS:  # 快速查找
    print("有效部门")
```

**为什么在本课中使用：** `PERSONA_DIRS` 用 `frozenset` 存储所有有效的部门目录名（如 `"engineering"`、`"design"` 等）。同步时用它过滤仓库文件树，只下载属于已知部门的人设文件。

### `Path.mkdir(parents=True, exist_ok=True)`（安全创建目录）

`parents=True` 自动创建所有不存在的父目录，`exist_ok=True` 在目录已存在时不报错。两者组合让目录创建变得安全可靠。

```python
from pathlib import Path

path = Path("/tmp/a/b/c")
path.mkdir(parents=True, exist_ok=True)  # 无论是否存在都不报错
```

**为什么在本课中使用：** 同步人设文件到本地目录时，目标目录可能不存在。`dest_dir.mkdir(parents=True, exist_ok=True)` 确保目录结构正确建立，无论是首次运行还是重复运行。

### `asyncio.get_running_loop()` 与 `loop.run_in_executor()`

`get_running_loop()` 获取当前正在运行的事件循环。`run_in_executor(None, func)` 在线程池中运行同步函数，并返回可 `await` 的结果——让同步代码不阻塞事件循环。

```python
import asyncio

def slow_sync_work():
    import time
    time.sleep(2)
    return "done"

async def main():
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, slow_sync_work)
    print(result)
```

**为什么在本课中使用：** `async_sync_personas()` 将同步的文件下载函数包装为异步版本。文件下载涉及大量阻塞 I/O，通过 `run_in_executor` 在线程池中执行，不会阻塞主 asyncio 事件循环。

### `*,`（强制关键字参数）

函数参数中的 `*,` 之后的参数必须以关键字形式传递，不能按位置传递。这提高了调用的可读性并防止参数顺序错误。

```python
def sync_personas(dest_dir, *, departments=None, force=False):
    pass

# sync_personas(path, {"eng"}, True)  # 错误！
sync_personas(path, departments={"eng"}, force=True)  # 正确
```

**为什么在本课中使用：** `sync_personas()` 的 `departments`、`force`、`progress_callback` 都是可选参数。使用 `*,` 强制以关键字传递，避免了调用时因参数顺序混乱而产生的 bug。

### `@pytest.fixture` 与 `@pytest.mark.asyncio`（异步测试）

`@pytest.fixture` 创建可复用的测试准备代码。`@pytest.mark.asyncio` 让测试函数可以是 `async def`，pytest 会自动管理事件循环。

```python
import pytest

@pytest.fixture
def registry():
    reg = ExpertRegistry()
    reg.register(some_persona)
    return reg

@pytest.mark.asyncio
async def test_route(registry):
    router = ExpertRouter(registry)
    result = await router.route("@coder hello", session_key="s1")
    assert result.source == "command"
```

**为什么在本课中使用：** 路由器的 `route()` 是异步方法，测试中必须用 `await` 调用。`@pytest.mark.asyncio` 让 pytest 可以运行异步测试。`@pytest.fixture` 为多个测试类提供预配置的 `registry` 和 `router` 实例。

### f-string 格式化字符串

f-string（以 `f` 开头的字符串）可以在花括号中嵌入 Python 表达式，是最简洁的字符串格式化方式。

```python
name = "Alice"
score = 95.5
print(f"Student: {name}, Score: {score:.1f}")
# 输出: Student: Alice, Score: 95.5
```

**为什么在本课中使用：** 路由器在构建专家列表、日志消息和 LLM 提示词时大量使用 f-string，如 `f"- @{p.slug} -- {p.name}: {p.description[:60]}"`，将人设信息格式化为可读的文本输出。
