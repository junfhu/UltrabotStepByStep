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
