# ultrabot/chunking/__init__.py
"""按通道对出站消息进行分块。"""

from ultrabot.chunking.chunker import (
    CHANNEL_CHUNK_LIMITS,
    DEFAULT_CHUNK_LIMIT,
    DEFAULT_CHUNK_MODE,
    ChunkMode,
    chunk_text,
    get_chunk_limit,
)

__all__ = [
    "CHANNEL_CHUNK_LIMITS",
    "DEFAULT_CHUNK_LIMIT",
    "DEFAULT_CHUNK_MODE",
    "ChunkMode",
    "chunk_text",
    "get_chunk_limit",
]
