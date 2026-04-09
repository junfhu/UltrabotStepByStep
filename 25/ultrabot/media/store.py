# ultrabot/media/store.py
"""带 TTL 生命周期管理的媒体文件存储。"""
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from loguru import logger


class MediaStore:
    """集中式媒体目录，带 TTL 清理。

    参数：
        base_dir: 存储媒体文件的根目录。
        ttl_seconds: 媒体文件的存活时间（默认 1 小时）。
        max_size_bytes: 允许的最大文件大小（默认 20MB）。
    """

    def __init__(self, base_dir: Path, ttl_seconds: int = 3600,
                 max_size_bytes: int = 20 * 1024 * 1024) -> None:
        self.base_dir = Path(base_dir)
        self.ttl_seconds = ttl_seconds
        self.max_size_bytes = max_size_bytes
        self.base_dir.mkdir(parents=True, exist_ok=True)
        logger.info("MediaStore initialised at {} (ttl={}s, max={}MB)",
                     base_dir, ttl_seconds, max_size_bytes // (1024 * 1024))

    def save(self, data: bytes, filename: str,
             content_type: str | None = None) -> dict[str, Any]:
        """保存媒体数据并返回元数据字典。"""
        if len(data) > self.max_size_bytes:
            raise ValueError(f"File too large: {len(data)} bytes (max {self.max_size_bytes})")

        media_id = f"{uuid.uuid4().hex[:12]}_{self._sanitize_filename(filename)}"
        path = self.base_dir / media_id
        path.write_bytes(data)

        if content_type is None:
            content_type = self._detect_mime(data, filename)

        logger.debug("Saved media: {} ({} bytes, {})", media_id, len(data), content_type)

        return {
            "id": media_id, "path": str(path), "size": len(data),
            "content_type": content_type, "filename": filename,
            "created_at": time.time(),
        }

    def save_from_path(self, source: Path,
                       content_type: str | None = None) -> dict[str, Any]:
        """将本地文件复制到媒体存储中。"""
        source = Path(source)
        if not source.exists():
            raise FileNotFoundError(f"Source file not found: {source}")
        return self.save(source.read_bytes(), source.name, content_type)

    def get(self, media_id: str) -> Path | None:
        path = self.base_dir / media_id
        return path if path.exists() else None

    def delete(self, media_id: str) -> bool:
        path = self.base_dir / media_id
        if path.exists():
            path.unlink()
            return True
        return False

    def cleanup(self) -> int:
        """移除过期文件。返回移除的文件数。"""
        now = time.time()
        removed = 0
        for path in self.base_dir.iterdir():
            if path.is_file():
                age = now - path.stat().st_mtime
                if age > self.ttl_seconds:
                    path.unlink()
                    removed += 1
        if removed:
            logger.info("MediaStore cleanup: removed {} expired file(s)", removed)
        return removed

    def list_files(self) -> list[dict[str, Any]]:
        files = []
        for path in sorted(self.base_dir.iterdir()):
            if path.is_file():
                stat = path.stat()
                files.append({
                    "id": path.name, "path": str(path), "size": stat.st_size,
                    "created_at": stat.st_mtime,
                    "age_seconds": time.time() - stat.st_mtime,
                })
        return files

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)
        return safe[:100] or "file"

    @staticmethod
    def _detect_mime(data: bytes, filename: str) -> str:
        """通过魔术字节 + 扩展名进行尽力而为的 MIME 检测。"""
        # 魔术字节
        if data[:8] == b'\x89PNG\r\n\x1a\n':
            return "image/png"
        if data[:3] == b'\xff\xd8\xff':
            return "image/jpeg"
        if data[:4] == b'GIF8':
            return "image/gif"
        if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
            return "image/webp"
        if data[:4] == b'%PDF':
            return "application/pdf"
        if data[:4] in (b'OggS',):
            return "audio/ogg"
        if data[:3] == b'ID3' or data[:2] == b'\xff\xfb':
            return "audio/mpeg"

        # 扩展名回退
        ext = Path(filename).suffix.lower()
        ext_map = {
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
            ".pdf": "application/pdf", ".mp3": "audio/mpeg", ".ogg": "audio/ogg",
            ".opus": "audio/opus", ".wav": "audio/wav", ".m4a": "audio/mp4",
            ".mp4": "video/mp4", ".webm": "video/webm", ".txt": "text/plain",
            ".json": "application/json", ".html": "text/html",
        }
        return ext_map.get(ext, "application/octet-stream")
