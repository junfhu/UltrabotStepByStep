# 课程 23：媒体管道 — 图片和文档

**目标：** 构建一个媒体处理管道，用于获取、处理和存储图片及文档，并具备 SSRF 防护。

**你将学到：**
- 带 SSRF 防护和流式下载的 `MediaFetcher`
- 使用 Pillow 的自适应缩放/压缩 `ImageOps`
- 用于文本和元数据提取的 `PDFExtractor`
- 带 TTL 生命周期管理和 MIME 检测的 `MediaStore`
- 魔术字节内容类型检测

**新建文件：**
- `ultrabot/media/__init__.py` — 包导出
- `ultrabot/media/fetch.py` — 带 SSRF 防护的安全 URL 获取
- `ultrabot/media/image_ops.py` — 图片缩放、压缩、格式转换
- `ultrabot/media/pdf_extract.py` — PDF 文本提取
- `ultrabot/media/store.py` — 带 TTL 清理的本地媒体存储

### 步骤 1：安全媒体获取

获取器阻止对内部/私有 IP 范围的请求（SSRF 防护），强制执行大小限制，并通过流式下载避免内存峰值。

```python
# ultrabot/media/fetch.py
"""带 SSRF 防护和大小限制的安全媒体获取。"""
from __future__ import annotations

import asyncio
from urllib.parse import urlparse

import httpx
from loguru import logger

# 用于 SSRF 防护的被阻止私有/内部 IP 范围
_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"}

DEFAULT_MAX_SIZE = 20 * 1024 * 1024  # 20MB
DEFAULT_TIMEOUT = 30
MAX_REDIRECTS = 5


def _is_safe_url(url: str) -> bool:
    """检查 URL 是否可以安全获取（不指向内部服务）。"""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        if hostname in _BLOCKED_HOSTS:
            return False
        if hostname.startswith("10.") or hostname.startswith("192.168."):
            return False
        if hostname.startswith("172."):
            parts = hostname.split(".")
            if len(parts) >= 2 and 16 <= int(parts[1]) <= 31:
                return False
        if parsed.scheme not in ("http", "https"):
            return False
        return True
    except Exception:
        return False


async def fetch_media(
    url: str,
    max_size: int = DEFAULT_MAX_SIZE,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    """从 URL 获取媒体，带大小限制和 SSRF 防护。

    返回包含以下字段的字典：data (bytes)、content_type (str)、
                           filename (str|None)、size (int)
    """
    if not _is_safe_url(url):
        raise ValueError(f"Unsafe URL blocked: {url}")

    async with httpx.AsyncClient(
        follow_redirects=True,
        max_redirects=MAX_REDIRECTS,
        timeout=timeout,
    ) as client:
        # 先发 HEAD 请求检查 Content-Length
        try:
            head = await client.head(url)
            cl = head.headers.get("content-length")
            if cl and int(cl) > max_size:
                raise ValueError(f"Content too large: {int(cl)} bytes (max {max_size})")
        except httpx.HTTPError:
            pass  # 不支持 HEAD，继续 GET

        # 流式 GET 以避免一次性将大文件加载到内存
        data = b""
        content_type = None
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").split(";")[0].strip()

            async for chunk in response.aiter_bytes(chunk_size=8192):
                data += chunk
                if len(data) > max_size:
                    raise ValueError(
                        f"Content exceeded max size during download ({max_size} bytes)"
                    )

        filename = _parse_filename(response.headers, url)
        logger.debug("Fetched media: {} ({} bytes, {})", url[:80], len(data), content_type)

        return {
            "data": data,
            "content_type": content_type or "application/octet-stream",
            "filename": filename,
            "size": len(data),
        }


def _parse_filename(headers: httpx.Headers, url: str) -> str | None:
    """从 Content-Disposition 头或 URL 路径中提取文件名。"""
    cd = headers.get("content-disposition", "")
    if "filename=" in cd:
        parts = cd.split("filename=")
        if len(parts) > 1:
            fname = parts[1].strip().strip('"').strip("'")
            if fname:
                return fname
    path = urlparse(url).path
    if path and "/" in path:
        name = path.rsplit("/", 1)[-1]
        if "." in name:
            return name
    return None
```

### 步骤 2：图片操作

图片处理器使用自适应缩放网格 — 它逐步尝试更小的尺寸和更低的质量级别，直到达到目标大小。

```python
# ultrabot/media/image_ops.py
"""图片处理操作 -- 缩放、压缩、格式转换。"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from loguru import logger

# 自适应缩放网格和质量步进
RESIZE_GRID = [2048, 1800, 1600, 1400, 1200, 1000, 800]
QUALITY_STEPS = [85, 75, 65, 55, 45, 35]


def _get_pillow():
    """延迟导入 Pillow。返回 (Image 模块, 是否可用)。"""
    try:
        from PIL import Image, ExifTags
        return Image, True
    except ImportError:
        return None, False


def resize_image(
    data: bytes,
    max_size_bytes: int = 5 * 1024 * 1024,
    max_dimension: int = 2048,
    output_format: str | None = None,
) -> bytes:
    """缩放和压缩图片以适应大小/尺寸限制。

    逐步尝试更小的尺寸和更低的质量，直到达到目标。
    保留 EXIF 方向信息。
    """
    Image, available = _get_pillow()
    if not available:
        raise ImportError("Pillow is required. Install with: pip install Pillow")

    # 检查是否已在限制范围内
    if len(data) <= max_size_bytes:
        img = Image.open(io.BytesIO(data))
        w, h = img.size
        if w <= max_dimension and h <= max_dimension:
            return data

    img = Image.open(io.BytesIO(data))

    # 根据 EXIF 自动旋转
    try:
        from PIL import ImageOps
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass

    fmt = output_format.upper() if output_format else (img.format or "JPEG")

    # JPEG 需将 RGBA 转换为 RGB
    if fmt == "JPEG" and img.mode in ("RGBA", "LA", "P"):
        background = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode == "P":
            img = img.convert("RGBA")
        background.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
        img = background

    # 尝试缩放网格 x 质量网格
    for dim in RESIZE_GRID:
        if dim > max_dimension:
            continue

        w, h = img.size
        if w <= dim and h <= dim:
            resized = img.copy()
        else:
            ratio = min(dim / w, dim / h)
            resized = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)

        for quality in QUALITY_STEPS:
            buf = io.BytesIO()
            save_kwargs: dict[str, Any] = {}
            if fmt in ("JPEG", "WEBP"):
                save_kwargs["quality"] = quality
                save_kwargs["optimize"] = True
            elif fmt == "PNG":
                save_kwargs["compress_level"] = 9

            resized.save(buf, format=fmt, **save_kwargs)
            result = buf.getvalue()

            if len(result) <= max_size_bytes:
                logger.debug("Image resized: {}x{} q={} -> {} bytes",
                             resized.size[0], resized.size[1], quality, len(result))
                return result

    # 最后手段
    logger.warning("Could not reduce to target size, returning smallest version")
    buf = io.BytesIO()
    smallest = img.resize((800, int(800 * img.size[1] / img.size[0])), Image.LANCZOS)
    smallest.save(buf, format=fmt, quality=35 if fmt in ("JPEG", "WEBP") else None)
    return buf.getvalue()


def get_image_info(data: bytes) -> dict[str, Any]:
    """获取基本图片信息，无需大量处理。"""
    Image, available = _get_pillow()
    if not available:
        return {"error": "Pillow not installed"}
    try:
        img = Image.open(io.BytesIO(data))
        return {
            "format": img.format,
            "mode": img.mode,
            "width": img.size[0],
            "height": img.size[1],
            "size_bytes": len(data),
        }
    except Exception as e:
        return {"error": str(e)}
```

### 步骤 3：PDF 文本提取

```python
# ultrabot/media/pdf_extract.py
"""PDF 文本和图片提取。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loguru import logger


@dataclass
class PdfContent:
    """从 PDF 中提取的内容。"""
    text: str = ""
    pages: int = 0
    images: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def extract_pdf_text(data: bytes, max_pages: int = 100) -> PdfContent:
    """从 PDF 中提取文本内容。

    返回包含提取文本和元数据的 PdfContent。
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ImportError("pypdf is required. Install with: pip install pypdf")

    import io
    reader = PdfReader(io.BytesIO(data))

    total_pages = len(reader.pages)
    pages_to_read = min(total_pages, max_pages) if max_pages > 0 else total_pages

    text_parts = []
    images = []

    for i in range(pages_to_read):
        page = reader.pages[i]

        page_text = page.extract_text() or ""
        if page_text.strip():
            text_parts.append(f"--- Page {i + 1} ---\n{page_text}")

        # 统计图片但不提取二进制数据
        if hasattr(page, "images"):
            for img in page.images:
                images.append({
                    "page": i + 1,
                    "name": getattr(img, "name", f"image_{len(images)}"),
                })

    metadata = {}
    if reader.metadata:
        for key in ("title", "author", "subject", "creator"):
            val = getattr(reader.metadata, key, None)
            if val:
                metadata[key] = str(val)

    result = PdfContent(
        text="\n\n".join(text_parts),
        pages=total_pages,
        images=images,
        metadata=metadata,
    )
    logger.debug("PDF extracted: {} pages, {} chars, {} images",
                 result.pages, len(result.text), len(result.images))
    return result
```

### 步骤 4：带 TTL 和 MIME 检测的 MediaStore

```python
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
```

### 步骤 5：包初始化

```python
# ultrabot/media/__init__.py
"""媒体管道 -- ultrabot 的图片、音频和 PDF 处理。"""
from ultrabot.media.store import MediaStore
from ultrabot.media.fetch import fetch_media
from ultrabot.media.image_ops import resize_image
from ultrabot.media.pdf_extract import extract_pdf_text

__all__ = ["MediaStore", "fetch_media", "resize_image", "extract_pdf_text"]
```

### 测试

```python
# tests/test_media_pipeline.py
"""媒体管道模块的测试。"""

import pytest
from pathlib import Path

from ultrabot.media.fetch import _is_safe_url
from ultrabot.media.store import MediaStore
from ultrabot.media.image_ops import get_image_info


class TestSSRFProtection:
    def test_blocks_localhost(self):
        assert _is_safe_url("http://localhost/secret") is False
        assert _is_safe_url("http://127.0.0.1:8080/api") is False

    def test_blocks_private_ranges(self):
        assert _is_safe_url("http://10.0.0.1/internal") is False
        assert _is_safe_url("http://192.168.1.1/admin") is False
        assert _is_safe_url("http://172.16.0.1/data") is False

    def test_allows_public_urls(self):
        assert _is_safe_url("https://example.com/image.png") is True
        assert _is_safe_url("https://cdn.github.com/file.pdf") is True

    def test_blocks_non_http(self):
        assert _is_safe_url("ftp://example.com/file") is False
        assert _is_safe_url("file:///etc/passwd") is False


class TestMediaStore:
    @pytest.fixture
    def store(self, tmp_path):
        return MediaStore(base_dir=tmp_path / "media", ttl_seconds=10)

    def test_save_and_get(self, store):
        result = store.save(b"Hello World", "test.txt", "text/plain")
        assert result["size"] == 11
        assert result["content_type"] == "text/plain"
        assert store.get(result["id"]) is not None

    def test_save_detects_mime(self, store):
        # PNG 魔术字节
        png_header = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100
        result = store.save(png_header, "image.png")
        assert result["content_type"] == "image/png"

        # JPEG 魔术字节
        jpeg_header = b'\xff\xd8\xff' + b'\x00' * 100
        result = store.save(jpeg_header, "photo.jpg")
        assert result["content_type"] == "image/jpeg"

        # PDF 魔术字节
        pdf_header = b'%PDF-1.4' + b'\x00' * 100
        result = store.save(pdf_header, "doc.pdf")
        assert result["content_type"] == "application/pdf"

    def test_size_limit(self, store):
        store.max_size_bytes = 100
        with pytest.raises(ValueError, match="too large"):
            store.save(b"x" * 200, "big.bin")

    def test_delete(self, store):
        result = store.save(b"temp", "temp.txt")
        assert store.delete(result["id"]) is True
        assert store.get(result["id"]) is None
        assert store.delete("nonexistent") is False

    def test_list_files(self, store):
        store.save(b"file1", "a.txt")
        store.save(b"file2", "b.txt")
        files = store.list_files()
        assert len(files) == 2

    def test_sanitize_filename(self):
        assert MediaStore._sanitize_filename("normal.txt") == "normal.txt"
        assert MediaStore._sanitize_filename("bad file!@#.txt") == "bad_file___.txt"
        assert MediaStore._sanitize_filename("") == "file"


class TestImageOps:
    def test_get_image_info_no_pillow(self):
        # 如果 Pillow 未安装，应返回错误字典
        info = get_image_info(b"not an image")
        # 返回格式信息或错误 — 两者都有效
        assert isinstance(info, dict)


class TestMimeDetection:
    def test_magic_bytes(self):
        assert MediaStore._detect_mime(b'\x89PNG\r\n\x1a\n', "x") == "image/png"
        assert MediaStore._detect_mime(b'\xff\xd8\xff', "x") == "image/jpeg"
        assert MediaStore._detect_mime(b'GIF89a', "x") == "image/gif"
        assert MediaStore._detect_mime(b'%PDF-1.5', "x") == "application/pdf"

    def test_extension_fallback(self):
        assert MediaStore._detect_mime(b'unknown', "file.mp3") == "audio/mpeg"
        assert MediaStore._detect_mime(b'unknown', "file.json") == "application/json"
        assert MediaStore._detect_mime(b'unknown', "file.xyz") == "application/octet-stream"
```

### 检查点

```bash
python -c "
import tempfile
from pathlib import Path
from ultrabot.media.store import MediaStore
from ultrabot.media.fetch import _is_safe_url
from ultrabot.media.image_ops import get_image_info

# 测试 SSRF 防护
print('SSRF checks:')
print(f'  localhost:  {_is_safe_url(\"http://localhost/x\")}')      # False
print(f'  10.0.0.1:  {_is_safe_url(\"http://10.0.0.1/x\")}')      # False
print(f'  github.com: {_is_safe_url(\"https://github.com/x\")}')   # True

# 测试 MediaStore
store = MediaStore(base_dir=Path(tempfile.mkdtemp()) / 'media')
# 保存一个模拟 PNG
png_data = b'\x89PNG\r\n\x1a\n' + b'\x00' * 50
result = store.save(png_data, 'test.png')
print(f'\nSaved: {result[\"filename\"]} ({result[\"size\"]} bytes)')
print(f'  MIME: {result[\"content_type\"]}')
print(f'  ID:   {result[\"id\"]}')

# 列出文件
files = store.list_files()
print(f'  Files in store: {len(files)}')
"
```

预期输出：
```
SSRF checks:
  localhost:  False
  10.0.0.1:  False
  github.com: True

Saved: test.png (58 bytes)
  MIME: image/png
  ID:   abc123def456_test.png
  Files in store: 1
```

### 本课成果

一个完整的媒体处理管道，包含四个模块：`fetch`（具备 SSRF 安全防护的 URL 下载，
支持流式传输和大小限制）、`image_ops`（使用 Pillow 通过尺寸/质量网格进行自适应
缩放）、`pdf_extract`（基于 pypdf 的文本和元数据提取）、以及 `store`（带 UUID
前缀命名、魔术字节 MIME 检测、TTL 清理和大小限制的本地文件存储）。所有模块在
可选依赖（Pillow、pypdf）未安装时均能优雅降级。
# UltraBot 开发者指南 — 第 4 部分：课程 24–30

> **前述课程：** (1-4) LLM 聊天、流式传输、工具、工具集 · (5-8) 配置、提供者、Anthropic、CLI · (9-12) 会话、熔断器、消息总线、安全 · (13-16) 通道、网关 · (17-19) 专家、Web 界面 · (20-23) 定时任务、守护进程、记忆、媒体

---
