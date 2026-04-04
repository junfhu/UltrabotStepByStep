# Ultrabot：30 课程开发指南
**从零开始构建一个生产级 AI 助手框架。**
本指南将带你从"向 LLM 问好"一步步走到一个完整的多提供者、多通道 AI 智能体，具备工具调用、记忆、安全防护和 Web 界面。每节课程都建立在上一节课的基础之上。每节课都包含可运行的代码和测试。  
本教程的主要思路来自于
- Nanobot (https://github.com/HKUDS/nanobot)
- Learn-Claude-Code (https://github.com/shareAI-lab/learn-claude-code/)

本课程设计由AI辅助下完成，因为课程自身也在不停修正，请参考 https://github.com/junfhu/UltrabotStepByStep，如果您觉得对您有帮助，请帮助点亮一颗星。  
本课程中使用的大模型提供商是火山引擎Code Plan，如果正好你也需要，可以使用我的邀请码获取9折优惠 https://volcengine.com/L/_01BJCkKdMc/  邀请码：HHCDB4J4）  



# 课程 12：安全守卫

**目标：** 添加一个安全层，对发送者进行速率限制、验证输入长度、阻止危险模式，并实施逐通道的访问控制。

**你将学到：**
- 使用基于双端队列的令牌桶实现滑动窗口速率限制
- 输入清理（长度限制、正则模式阻止、控制字符移除）
- 逐通道的访问控制允许列表
- 将多个守卫组合在单一门面之后

**新建文件：**
- `ultrabot/security/__init__.py` — 公共重导出
- `ultrabot/security/guard.py` — `RateLimiter`、`InputSanitizer`、`AccessController`、`SecurityGuard`

### 步骤 1：安全配置

创建 `ultrabot/security/guard.py`：

```python
"""安全执行 — 速率限制、输入清理、访问控制。"""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, field

from loguru import logger
from ultrabot.bus.events import InboundMessage


@dataclass
class SecurityConfig:
    """所有安全子系统的配置。

    Attributes:
        rpm:              每个发送者每分钟允许的请求数。
        burst:            在 rpm 之上的额外突发容量，用于短暂的峰值。
        max_input_length: 单条消息的最大字符数。
        blocked_patterns: 内容中不得出现的正则模式。
        allow_from:       逐通道的发送者 ID 允许列表。
                          ``"*"`` 表示允许所有发送者。
    """
    rpm: int = 30
    burst: int = 5
    max_input_length: int = 8192
    blocked_patterns: list[str] = field(default_factory=list)
    allow_from: dict[str, list[str]] = field(default_factory=dict)
```

### 步骤 2：速率限制器 — 滑动窗口

速率限制器为每个发送者维护一个时间戳双端队列。每次请求时，
我们清除超过 60 秒的时间戳，然后检查发送者是否还有剩余容量。

```python
class RateLimiter:
    """使用每个发送者一个双端队列的滑动窗口速率限制器。"""

    def __init__(self, rpm: int = 30, burst: int = 5) -> None:
        self.rpm = rpm
        self.burst = burst
        self._window = 60.0
        self._timestamps: dict[str, deque[float]] = {}

    async def acquire(self, sender_id: str) -> bool:
        """尝试消费一个令牌。允许则返回 True。"""
        now = time.monotonic()
        if sender_id not in self._timestamps:
            self._timestamps[sender_id] = deque()

        dq = self._timestamps[sender_id]

        # 清除窗口外的时间戳。
        while dq and (now - dq[0]) > self._window:
            dq.popleft()

        capacity = self.rpm + self.burst
        if len(dq) >= capacity:
            logger.warning("Rate limit exceeded for sender {}", sender_id)
            return False

        dq.append(now)
        return True
```

**为什么不使用固定补充速率的令牌桶？** 滑动窗口方法更简单，
并且能在任意 60 秒窗口内给出精确计数。

### 步骤 3：输入清理器

```python
class InputSanitizer:
    """验证和清理原始消息内容。"""

    @staticmethod
    def validate_length(content: str, max_length: int) -> bool:
        return len(content) <= max_length

    @staticmethod
    def check_blocked_patterns(content: str, patterns: list[str]) -> str | None:
        """返回第一个匹配的模式，或 None。"""
        for pattern in patterns:
            try:
                if re.search(pattern, content, re.IGNORECASE):
                    return pattern
            except re.error:
                logger.error("Invalid blocked regex: {}", pattern)
        return None

    @staticmethod
    def sanitize(content: str) -> str:
        """剥除空字节和 ASCII 控制字符（保留制表符、换行符、回车符）。"""
        content = content.replace("\x00", "")
        content = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", "", content)
        return content
```

### 步骤 4：访问控制器

```python
class AccessController:
    """基于通道的发送者允许列表。

    未在配置中列出的通道默认开放（等同于 ``"*"``）。
    """

    def __init__(self, allow_from: dict[str, list[str]] | None = None) -> None:
        self._allow_from = allow_from or {}

    def is_allowed(self, channel: str, sender_id: str) -> bool:
        allowed = self._allow_from.get(channel)
        if allowed is None:
            return True                  # 无规则 = 开放
        if "*" in allowed:
            return True
        return sender_id in allowed
```

### 步骤 5：SecurityGuard 门面

三个子系统全部组合在一个 `check_inbound` 方法后面，
返回 `(allowed, reason)`：

```python
class SecurityGuard:
    """统一的安全门面。"""

    def __init__(self, config: SecurityConfig | None = None) -> None:
        self.config = config or SecurityConfig()
        self.rate_limiter = RateLimiter(
            rpm=self.config.rpm, burst=self.config.burst
        )
        self.sanitizer = InputSanitizer()
        self.access_controller = AccessController(
            allow_from=self.config.allow_from
        )

    async def check_inbound(
        self, message: InboundMessage
    ) -> tuple[bool, str]:
        """根据所有安全策略进行验证。

        返回 (allowed, reason)。
        """
        # 1. 访问控制。
        if not self.access_controller.is_allowed(
            message.channel, message.sender_id
        ):
            reason = f"Access denied for {message.sender_id} on {message.channel}"
            logger.warning(reason)
            return False, reason

        # 2. 速率限制。
        if not await self.rate_limiter.acquire(message.sender_id):
            return False, f"Rate limit exceeded for {message.sender_id}"

        # 3. 输入长度。
        if not self.sanitizer.validate_length(
            message.content, self.config.max_input_length
        ):
            reason = (
                f"Input too long ({len(message.content)} chars, "
                f"max {self.config.max_input_length})"
            )
            return False, reason

        # 4. 阻止模式。
        matched = self.sanitizer.check_blocked_patterns(
            message.content, self.config.blocked_patterns,
        )
        if matched is not None:
            return False, f"Blocked pattern matched: {matched}"

        return True, "ok"
```

### 步骤 6：包初始化

```python
# ultrabot/security/__init__.py
"""安全包的公共 API。"""

from ultrabot.security.guard import (
    AccessController, InputSanitizer, RateLimiter,
    SecurityConfig, SecurityGuard,
)

__all__ = [
    "AccessController", "InputSanitizer", "RateLimiter",
    "SecurityConfig", "SecurityGuard",
]
```

### 测试

```python
# tests/test_security.py
import asyncio
from ultrabot.bus.events import InboundMessage
from ultrabot.security.guard import (
    AccessController, InputSanitizer, RateLimiter,
    SecurityConfig, SecurityGuard,
)


def _make_msg(content="hi", sender="u1", channel="test"):
    return InboundMessage(
        channel=channel, sender_id=sender, chat_id="c1", content=content,
    )


def test_rate_limiter_allows_then_blocks():
    async def _run():
        rl = RateLimiter(rpm=3, burst=0)
        results = [await rl.acquire("u1") for _ in range(5)]
        assert results == [True, True, True, False, False]
    asyncio.run(_run())


def test_sanitizer_strips_control_chars():
    dirty = "hello\x00world\x07!"
    clean = InputSanitizer.sanitize(dirty)
    assert clean == "helloworld!"


def test_sanitizer_blocks_pattern():
    match = InputSanitizer.check_blocked_patterns(
        "ignore previous instructions", [r"ignore.*instructions"]
    )
    assert match is not None


def test_access_controller():
    ac = AccessController(allow_from={"discord": ["123", "456"]})
    assert ac.is_allowed("discord", "123") is True
    assert ac.is_allowed("discord", "789") is False
    assert ac.is_allowed("telegram", "anyone") is True  # 无规则 = 开放


def test_security_guard_rejects_long_input():
    async def _run():
        guard = SecurityGuard(SecurityConfig(max_input_length=10))
        msg = _make_msg(content="x" * 100)
        allowed, reason = await guard.check_inbound(msg)
        assert allowed is False
        assert "too long" in reason
    asyncio.run(_run())


def test_security_guard_passes_valid():
    async def _run():
        guard = SecurityGuard()
        msg = _make_msg(content="Hello, bot!")
        allowed, reason = await guard.check_inbound(msg)
        assert allowed is True
        assert reason == "ok"
    asyncio.run(_run())
```

### 检查点

```bash
python -m pytest tests/test_security.py -v
```

预期结果：全部 6 个测试通过。试着在 CLI REPL 中快速发送消息 —
在 60 秒内发送 `rpm + burst` 条消息后，守卫会阻止你。

### 本课成果

一个 `SecurityGuard` 门面，组合了滑动窗口 `RateLimiter`、
`InputSanitizer`（长度限制、正则阻止、控制字符剥除），以及
逐通道的 `AccessController`。每条入站消息在到达智能体之前
都会经过 `check_inbound()` 检查。

---

## 本课使用的 Python 知识

### `from __future__ import annotations`（延迟注解评估）

这是 Python 3.7 引入的特性，让类型注解在运行时不被立即求值，而是保存为字符串。这样可以使用尚未定义的类型名，也让 `str | None` 这样的新语法在旧版本中可用。

```python
from __future__ import annotations

def greet(name: str | None = None) -> str:
    return f"Hello, {name or 'World'}!"
```

**为什么在本课中使用：** 代码中大量使用了 `str | None`、`dict[str, list[str]]` 等现代类型注解语法，这行导入确保在 Python 3.9 等稍旧版本中也能正常运行。

### `re` 模块（正则表达式）

Python 内置的 `re` 模块提供正则表达式支持，可以用来搜索、匹配和替换字符串中的模式。

```python
import re

# 搜索：如果找到匹配，返回 Match 对象；否则返回 None
result = re.search(r"hello", "say hello world", re.IGNORECASE)

# 替换：把匹配的部分替换掉
clean = re.sub(r"[0-9]+", "", "abc123def")  # "abcdef"
```

**为什么在本课中使用：** `InputSanitizer` 用 `re.search()` 检测消息中是否包含危险模式（如 "ignore.*instructions"），用 `re.sub()` 清除控制字符。正则表达式是处理文本安全验证的利器。

### `re.IGNORECASE` 标志

`re.IGNORECASE`（简写 `re.I`）是正则表达式的编译标志，让匹配时忽略大小写。

```python
import re
re.search(r"hello", "HELLO WORLD", re.IGNORECASE)  # 匹配成功
```

**为什么在本课中使用：** 阻止模式检查时使用了 `re.IGNORECASE`，因为恶意输入可能用大小写混写来绕过检测，比如 "Ignore Previous Instructions"。

### `re.error` 异常

当正则表达式字符串语法错误时，`re` 模块会抛出 `re.error` 异常。捕获它可以防止一个错误的正则模式导致整个程序崩溃。

```python
import re
try:
    re.search(r"[invalid", "text")  # 方括号没闭合
except re.error:
    print("正则表达式语法错误！")
```

**为什么在本课中使用：** `blocked_patterns` 是用户配置的正则列表，用户可能写出无效的正则。`check_blocked_patterns` 方法捕获 `re.error`，记录错误日志但不让程序崩溃。

### `time.monotonic()`（单调时钟）

`time.monotonic()` 返回一个只会递增的时间值（单位：秒），不受系统时钟调整的影响。它适合用来测量时间间隔，但不能用来获取"现在几点"。

```python
import time

start = time.monotonic()
# ... 做一些操作 ...
elapsed = time.monotonic() - start
print(f"耗时 {elapsed:.2f} 秒")
```

**为什么在本课中使用：** 速率限制器需要精确测量"过去 60 秒内发送了多少请求"。使用 `monotonic()` 而不是 `time.time()`，是因为系统时钟可能被手动调整（如 NTP 同步），而单调时钟保证只增不减，不会出现时间"倒流"。

### `collections.deque`（双端队列）

`deque`（读作 "deck"）是一种两端都能高效添加和删除元素的数据结构。从左端 `popleft()` 或右端 `pop()` 都是 O(1) 操作，比普通列表的 `pop(0)` 快得多。

```python
from collections import deque

dq = deque()
dq.append(1)       # 右端添加: [1]
dq.append(2)       # 右端添加: [1, 2]
dq.appendleft(0)   # 左端添加: [0, 1, 2]
dq.popleft()       # 左端移除: [1, 2]，返回 0
```

**为什么在本课中使用：** 滑动窗口速率限制器为每个发送者维护一个时间戳队列。新请求从右端 `append`，过期的时间戳从左端 `popleft` 清除。`deque` 在两端操作都是 O(1)，非常适合这种"从一端进、从另一端出"的场景。

### `@dataclass` 装饰器（数据类）

`@dataclass` 自动为类生成 `__init__`、`__repr__`、`__eq__` 等方法，只需声明字段和类型即可。

```python
from dataclasses import dataclass

@dataclass
class Config:
    host: str = "localhost"
    port: int = 8080
```

**为什么在本课中使用：** `SecurityConfig` 是一个纯配置容器，包含 `rpm`、`burst`、`max_input_length` 等参数。使用 `@dataclass` 可以简洁地定义带默认值的配置字段。

### `field(default_factory=...)` 数据类字段工厂

当 `@dataclass` 字段的默认值是可变类型（如 `list`、`dict`）时，必须使用 `field(default_factory=...)` 来避免所有实例共享同一个对象。

```python
from dataclasses import dataclass, field

@dataclass
class Config:
    tags: list[str] = field(default_factory=list)
    options: dict = field(default_factory=dict)
```

**为什么在本课中使用：** `SecurityConfig` 中的 `blocked_patterns`（列表）和 `allow_from`（字典）都是可变类型，使用 `default_factory` 确保每个配置实例有独立的列表和字典。

### `@staticmethod` 静态方法

`@staticmethod` 装饰的方法不需要访问实例（`self`）或类（`cls`），它就像一个普通函数，只是逻辑上归属于这个类。

```python
class MathHelper:
    @staticmethod
    def add(a, b):
        return a + b

# 不需要创建实例就能调用
result = MathHelper.add(3, 5)
```

**为什么在本课中使用：** `InputSanitizer` 的方法（`validate_length`、`check_blocked_patterns`、`sanitize`）不需要访问任何实例状态，它们是纯粹的输入→输出函数。声明为 `@staticmethod` 明确表达了这一点，也允许在不创建实例的情况下直接调用。

### `dict[str, deque[float]]` 嵌套类型提示

Python 的类型提示支持嵌套，可以精确描述复杂的数据结构。

```python
# 一个字典：键是字符串，值是浮点数双端队列
timestamps: dict[str, deque[float]] = {}

# 一个字典：键是字符串，值是字符串列表
allow_from: dict[str, list[str]] = {}
```

**为什么在本课中使用：** 速率限制器用 `dict[str, deque[float]]` 存储每个发送者的时间戳队列，访问控制器用 `dict[str, list[str]]` 存储每个通道的允许发送者列表。精确的类型提示帮助开发者理解数据结构，也让 IDE 提供更好的补全。

### `tuple[bool, str]` 返回多个值

Python 函数可以返回元组（tuple），调用者可以用解包语法同时接收多个值。在类型注解中写为 `tuple[bool, str]`。

```python
def check(value: int) -> tuple[bool, str]:
    if value > 0:
        return True, "ok"
    return False, "must be positive"

allowed, reason = check(-1)
```

**为什么在本课中使用：** `SecurityGuard.check_inbound()` 返回 `(allowed, reason)` 元组。调用者不仅能知道消息是否被允许，还能知道被拒绝的原因，便于记录日志或反馈给用户。

### `str.replace()` 字符串替换

`str.replace(old, new)` 返回一个新字符串，其中所有 `old` 子串都被替换为 `new`。

```python
text = "hello\x00world"
clean = text.replace("\x00", "")  # "helloworld"
```

**为什么在本课中使用：** `sanitize` 方法用 `content.replace("\x00", "")` 移除空字节（null byte），这是常见的安全清理步骤，防止空字节注入攻击。

### `or` 运算符用于默认值

Python 中 `a or b` 在 `a` 为假值（`None`、`{}`、`[]`、`0`、`""` 等）时返回 `b`。这是一种简洁的默认值写法。

```python
config = user_config or {}  # 如果 user_config 是 None，就用空字典
name = input_name or "Anonymous"
```

**为什么在本课中使用：** `AccessController.__init__` 中 `self._allow_from = allow_from or {}`，当传入 `None` 时自动使用空字典，避免后续代码需要判断 `None`。`SecurityGuard.__init__` 中也用了 `config or SecurityConfig()` 来提供默认配置。

### `async def` / `await`（异步编程）

`async def` 定义异步函数（协程），`await` 用于等待一个异步操作完成。异步编程让程序在等待 I/O（如网络请求）时不会阻塞，可以去处理其他任务。

```python
import asyncio

async def fetch_data():
    await asyncio.sleep(1)  # 模拟耗时操作，不阻塞
    return "data"

async def main():
    result = await fetch_data()
    print(result)
```

**为什么在本课中使用：** `RateLimiter.acquire()` 和 `SecurityGuard.check_inbound()` 都是异步方法，因为它们将被消息总线的异步分发循环调用。虽然当前的速率限制逻辑本身不涉及 I/O，但异步接口为未来扩展（如使用 Redis 做分布式速率限制）留下了空间。

### 列表推导式（List Comprehension）

列表推导式是 Python 创建列表的简洁语法，格式为 `[表达式 for 变量 in 可迭代对象]`。

```python
squares = [x * x for x in range(5)]  # [0, 1, 4, 9, 16]
results = [await rl.acquire("u1") for _ in range(5)]  # 连续调用5次
```

**为什么在本课中使用：** 测试代码中 `[await rl.acquire("u1") for _ in range(5)]` 用列表推导式连续发送 5 次请求，一行代码就完成了"验证前 3 次通过、后 2 次被拒绝"的测试。

### 门面模式（Facade Pattern）

门面模式是一种设计模式：将多个复杂的子系统组合在一个简单的统一接口后面。使用者只需要跟门面交互，不需要了解内部有几个子系统。

```python
class SecurityGuard:
    """统一门面 — 一个方法搞定所有安全检查。"""
    def __init__(self):
        self.rate_limiter = RateLimiter()
        self.sanitizer = InputSanitizer()
        self.access_controller = AccessController()

    async def check_inbound(self, message):
        # 依次调用三个子系统
        ...
```

**为什么在本课中使用：** `SecurityGuard` 就是门面，它把 `RateLimiter`、`InputSanitizer`、`AccessController` 三个独立组件组合起来。外部代码只需调用 `guard.check_inbound(msg)` 一个方法，不需要分别跟三个子系统打交道。

### `while` 循环清理过期数据（滑动窗口）

`while` 循环配合条件判断，可以实现"持续清除不满足条件的数据"。

```python
from collections import deque

dq = deque([1.0, 2.0, 3.0, 100.0])
threshold = 50.0

while dq and dq[0] < threshold:
    dq.popleft()  # 移除过期的数据
```

**为什么在本课中使用：** 速率限制器的 `acquire` 方法用 `while dq and (now - dq[0]) > self._window` 清除超过 60 秒的旧时间戳，实现了"滑动窗口"效果 — 只统计最近 60 秒内的请求数。

### `__all__` 模块导出控制

`__all__` 是一个列表，定义了当其他模块使用 `from package import *` 时，哪些名字会被导出。

```python
__all__ = ["SecurityGuard", "SecurityConfig", "RateLimiter"]
```

**为什么在本课中使用：** `ultrabot/security/__init__.py` 用 `__all__` 明确声明包的公共 API，让使用者知道应该用哪些类，同时隐藏内部实现细节。
