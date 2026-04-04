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
