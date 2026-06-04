"""sisoul friend · Petname (本地昵称 mapping).

P2-CD: ~/.sisoul/petnames.json 持久化 did:key → petname 映射.

设计要点:
- 纯本地, 不上链 / 不广播 (petname 是本端用户自定义, 不全局唯一).
- did 作 key, petname 作 value (str), 允许 update/remove.
- 文件不存在 → 视作空 mapping; JSON 损坏 → raise PetnameError (拒绝 silent reset).
- API: set / get / list_all / remove + path 解析 + load/save.
- did 校验: 必须 did:* 开头 (did:key:z... / did:sisoul:* 都允许).
- petname 校验: 非空 str, ≤ 64 字符, 不含控制字符.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

__all__ = [
    "DEFAULT_PETNAME_PATH",
    "PetnameError",
    "PetnameStore",
    "format_did_short",
]

DEFAULT_PETNAME_PATH = Path.home() / ".sisoul" / "petnames.json"
MAX_PETNAME_LEN = 64


class PetnameError(Exception):
    """petname store root error."""


def _validate_did(did: str) -> None:
    if not isinstance(did, str) or not did.startswith("did:"):
        raise PetnameError(f"did 必须 'did:' 开头 str, 实际: {did!r}")


def _validate_petname(name: str) -> None:
    if not isinstance(name, str) or not name.strip():
        raise PetnameError("petname 必须非空 str")
    if len(name) > MAX_PETNAME_LEN:
        raise PetnameError(f"petname 长度 ≤ {MAX_PETNAME_LEN}, 实际 {len(name)}")
    if any(ord(c) < 0x20 for c in name):
        raise PetnameError("petname 不能含控制字符")


def format_did_short(did: str, *, head: int = 8, tail: int = 4) -> str:
    """did:key:z6Mk... 缩写: 前 head + '…' + 后 tail."""
    if not isinstance(did, str) or len(did) <= head + tail + 1:
        return did
    return f"{did[:head]}…{did[-tail:]}"


class PetnameStore:
    """本地 petname mapping (did → petname).

    线程不安全 (单进程 CLI 调用一次即退); 多进程并发请上层锁.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path: Path = Path(path) if path is not None else DEFAULT_PETNAME_PATH
        self._data: dict[str, str] = {}
        self._loaded = False

    # ── persistence ─────────────────────────────────────────────────────────

    def load(self) -> "PetnameStore":
        if not self.path.exists():
            self._data = {}
            self._loaded = True
            return self
        try:
            raw = self.path.read_text(encoding="utf-8")
            obj = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError as e:
            raise PetnameError(f"petnames.json JSON 损坏: {e}") from e
        if not isinstance(obj, dict):
            raise PetnameError(f"petnames.json 顶层必须 dict, 实际 {type(obj).__name__}")
        # 容忍 value 非 str (跳过 + 警告): 转 str 即可
        self._data = {str(k): str(v) for k, v in obj.items() if isinstance(k, str)}
        self._loaded = True
        return self

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return self.path

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    # ── API ─────────────────────────────────────────────────────────────────

    def set(self, did: str, petname: str) -> None:
        _validate_did(did)
        _validate_petname(petname)
        self._ensure_loaded()
        self._data[did] = petname.strip()
        self.save()

    def get(self, did: str, default: Optional[str] = None) -> Optional[str]:
        self._ensure_loaded()
        return self._data.get(did, default)

    def remove(self, did: str) -> bool:
        self._ensure_loaded()
        if did in self._data:
            del self._data[did]
            self.save()
            return True
        return False

    def list_all(self) -> dict[str, str]:
        self._ensure_loaded()
        return dict(self._data)

    def __len__(self) -> int:
        self._ensure_loaded()
        return len(self._data)

    def __contains__(self, did: object) -> bool:
        self._ensure_loaded()
        return isinstance(did, str) and did in self._data

    def display_name(self, did: str) -> str:
        """petname 优先, 没设则返回 did 缩写."""
        name = self.get(did)
        if name:
            return name
        return format_did_short(did)
