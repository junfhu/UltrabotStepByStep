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
