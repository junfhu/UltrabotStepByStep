"""按通道对出站消息进行分块。"""

from __future__ import annotations

from enum import Enum


class ChunkMode(str, Enum):
    """拆分策略。"""
    LENGTH = "length"        # 按字符限制拆分，优先在空白处断开
    PARAGRAPH = "paragraph"  # 按空行边界拆分


# ── 平台上限（字符数） ──────────────────────────────────
# 每个通道驱动可以覆盖这些值，但以下是合理的默认值。
CHANNEL_CHUNK_LIMITS: dict[str, int] = {
    "telegram": 4096,
    "discord":  2000,
    "slack":    4000,
    "feishu":   30000,
    "qq":       4500,
    "wecom":    2048,
    "weixin":   2048,
    "webui":    0,          # 0 = 无限制（Web UI 会完整流式传输响应）
}

DEFAULT_CHUNK_LIMIT = 4000
DEFAULT_CHUNK_MODE = ChunkMode.LENGTH


def get_chunk_limit(channel: str, override: int | None = None) -> int:
    """返回 *channel* 的分块限制。0 表示无限制。"""
    if override is not None and override > 0:
        return override
    return CHANNEL_CHUNK_LIMITS.get(channel, DEFAULT_CHUNK_LIMIT)

def chunk_text(
    text: str,
    limit: int,
    mode: ChunkMode = ChunkMode.LENGTH,
) -> list[str]:
    """将 *text* 拆分为遵守 *limit* 的分块。

    - limit <= 0 → 将完整文本作为一个分块返回（不拆分）。
    - LENGTH 模式 → 优先在换行/空白处断开，感知代码围栏。
    - PARAGRAPH 模式 → 在空行处拆分，对过大的段落回退到 LENGTH 模式。
    """
    if not text:
        return []
    if limit <= 0:
        return [text]
    if len(text) <= limit:
        return [text]

    if mode == ChunkMode.PARAGRAPH:
        return _chunk_by_paragraph(text, limit)
    return _chunk_by_length(text, limit)

def _chunk_by_length(text: str, limit: int) -> list[str]:
    """按 *limit* 拆分，优先在换行/空白边界处断开。
    
    Markdown 围栏感知：不会在 ``` 代码块内部拆分。
    """
    chunks: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break

        candidate = remaining[:limit]

        # ── 代码围栏保护 ───────────────────────────
        # 统计开启/关闭围栏的数量。奇数表示我们在代码块内部。
        fence_count = candidate.count("```")
        if fence_count % 2 == 1:
            # 找到最后一个开启围栏之后的关闭围栏
            fence_end = remaining.find("```", candidate.rfind("```") + 3)
            if fence_end != -1 and fence_end + 3 <= len(remaining):
                split_at = fence_end + 3
                # 对齐到关闭围栏之后的下一个换行
                nl = remaining.find("\n", split_at)
                if nl != -1 and nl < split_at + 10:
                    split_at = nl + 1
                chunks.append(remaining[:split_at])
                remaining = remaining[split_at:]
                continue

        # ── 寻找最佳断开点 ───────────────────────
        # 优先级：双换行 > 单换行 > 空格
        best = -1
        for sep in ["\n\n", "\n", " "]:
            pos = candidate.rfind(sep)
            if pos > limit // 4:          # 不要断得太早
                best = pos + len(sep)
                break

        if best > 0:
            chunks.append(remaining[:best].rstrip())
            remaining = remaining[best:].lstrip()
        else:
            # 没有合适的断开点 — 硬拆分
            chunks.append(remaining[:limit])
            remaining = remaining[limit:]

    return [c for c in chunks if c.strip()]

def _chunk_by_paragraph(text: str, limit: int) -> list[str]:
    """按段落边界（空行）拆分。
    
    对于过大的段落，回退到基于长度的拆分。
    """
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        # 单个段落超过限制 → 回退到基于长度的拆分
        if len(para) > limit:
            if current:
                chunks.append(current.rstrip())
                current = ""
            chunks.extend(_chunk_by_length(para, limit))
            continue

        # 尝试追加到当前分块
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                chunks.append(current.rstrip())
            current = para

    if current:
        chunks.append(current.rstrip())

    return [c for c in chunks if c.strip()]
