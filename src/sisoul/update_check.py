"""Update check — alpha 轻量新版本通知 (2026-06-10).

无任何 sisoul 自营服务: 直接拉 GitHub raw 的 pyproject.toml 比对 version 字符串
(main 只前进, 不同即视为有新版). 结果缓存 24h 在 vault, 不每次都打网络.

通知路径:
- daemon 启动后台查一次 → 有新版 print log + SSE ``update.available``
- GET /sisoul/update/check → PWA TopBar 角标
- CLI ``sisoul update`` → 真跑 git pull + pip install (装在 git clone 时)
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from sisoul import __version__

RAW_PYPROJECT_URL = (
    "https://raw.githubusercontent.com/akige/sisoul/main/pyproject.toml"
)
CACHE_TTL_SEC = 24 * 3600


def _cache_path() -> Path:
    vault = Path(
        os.environ.get("SISOUL_VAULT", str(Path.home() / ".sisoul"))
    ).expanduser()
    return vault / "update_check.json"


def fetch_latest_version(timeout: float = 6.0) -> Optional[str]:
    """拉 main 分支 pyproject.toml 解析 version. 失败返 None (绝不 raise)."""
    try:
        import httpx

        resp = httpx.get(RAW_PYPROJECT_URL, timeout=timeout, follow_redirects=True)
        if resp.status_code != 200:
            return None
        m = re.search(r'^version\s*=\s*"([^"]+)"', resp.text, re.MULTILINE)
        return m.group(1) if m else None
    except Exception:  # noqa: BLE001 — 离线 / 被墙 / GitHub 挂都静默
        return None


def check_update(force: bool = False, timeout: float = 6.0) -> dict[str, Any]:
    """带 24h 缓存的更新检查.

    Returns:
        {current, latest, update_available, checked_at, source}
        latest=None 表示检查失败 (离线等), update_available=False.
    """
    cache = _cache_path()
    if not force and cache.exists():
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            if time.time() - float(cached.get("checked_at", 0)) < CACHE_TTL_SEC:
                cached["source"] = "cache"
                return cached
        except Exception:  # noqa: BLE001
            pass

    latest = fetch_latest_version(timeout=timeout)
    result: dict[str, Any] = {
        "current": __version__,
        "latest": latest,
        "update_available": bool(latest) and latest != __version__,
        "checked_at": int(time.time()),
        "source": "network",
    }
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(result), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return result


def repo_root() -> Optional[Path]:
    """sisoul 装在 git clone 里 (install.sh 路径) → 返 repo 根, 否则 None."""
    try:
        import sisoul

        p = Path(sisoul.__file__).resolve()
        for parent in p.parents:
            if (parent / ".git").is_dir() and (parent / "pyproject.toml").exists():
                return parent
    except Exception:  # noqa: BLE001
        pass
    return None


__all__ = ["check_update", "fetch_latest_version", "repo_root", "RAW_PYPROJECT_URL"]
