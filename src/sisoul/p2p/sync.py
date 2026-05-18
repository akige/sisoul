"""sisoul p2p · vault sync 协议 (Phase 3 W31-W36 · 波 4 dev-A).

§28 §1.1 模块 9.

协议设计 (简化版, Phase 3 W31-W36):

1. ``Inventory`` — vault 文件清单. 每文件: relative_path / mtime_ns / size / sha256.
   建 inventory 不读文件内容, 只 stat + sha256.

2. **announce 阶段**: alice → bob 发 INVENTORY_REQUEST; bob 回 INVENTORY_RESPONSE.
   反向同理 (双向都 sync).

3. **diff 阶段**: alice 对比 own inventory vs bob inventory, 计算 diff:
   - bob 有 alice 无 → alice 拉
   - alice 有 bob 无 → alice 推
   - 两边都有, mtime 不同 → newer wins (mtime_ns 大的一方为 truth)
   - 两边都有 mtime 接近 (< CONFLICT_WINDOW_NS) 但 hash 不同 → CONFLICT, 写
     conflict log, 不自动 merge (Phase 4 用 CRDT)

4. **transfer 阶段**: 推 / 拉 file 内容 (加密). 一文件一 FILE_CHUNK message.

5. **commit 阶段**: 写本地 (落盘 atomic temp + rename).

报文格式 (msgpack 风格 dict, 实际 JSON; 简化便于 debug):
- type: "INVENTORY_REQUEST" | "INVENTORY_RESPONSE" | "FILE_REQUEST" |
        "FILE_CHUNK" | "FILE_END" | "ACK" | "ERROR"
- payload: 各 type 具体内容

加密: encrypt(p2p_key, json_serialize(message)). 双方同 BIP-39 seed 派同 p2p_key.

冲突文件命名: ``<original>.conflict-<peer_short_id>-<mtime_ns>``
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


# 冲突窗口: 同 file 两 mtime 差 < 此值 + hash 不同 ⇒ conflict (不自动 newer wins)
CONFLICT_WINDOW_NS: int = 2 * 1_000_000_000  # 2 秒


# ── 数据类 ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FileMeta:
    """vault inventory 单文件元数据."""

    rel_path: str
    mtime_ns: int
    size: int
    sha256_hex: str

    def to_dict(self) -> dict:
        return {
            "rel_path": self.rel_path,
            "mtime_ns": self.mtime_ns,
            "size": self.size,
            "sha256_hex": self.sha256_hex,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FileMeta":
        return cls(
            rel_path=d["rel_path"],
            mtime_ns=int(d["mtime_ns"]),
            size=int(d["size"]),
            sha256_hex=d["sha256_hex"],
        )


@dataclass
class Inventory:
    """vault inventory: rel_path → FileMeta."""

    files: dict[str, FileMeta] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"files": [m.to_dict() for m in self.files.values()]}

    @classmethod
    def from_dict(cls, d: dict) -> "Inventory":
        inv = cls()
        for item in d.get("files", []):
            m = FileMeta.from_dict(item)
            inv.files[m.rel_path] = m
        return inv


@dataclass
class SyncDiff:
    """sync 双方对比结果."""

    pull: list[str] = field(default_factory=list)  # 本端要拉的 rel_path
    push: list[str] = field(default_factory=list)  # 本端要推的 rel_path
    conflicts: list[str] = field(default_factory=list)  # 冲突 rel_path


@dataclass
class ConflictRecord:
    """conflict 记录 (写 conflict log)."""

    rel_path: str
    local_mtime_ns: int
    remote_mtime_ns: int
    local_sha256: str
    remote_sha256: str
    peer_id: str
    detected_at: float = field(default_factory=time.time)
    resolution: str = "manual"  # 默认人工; Phase 4 可加 "newer-wins-auto"

    def to_dict(self) -> dict:
        return {
            "rel_path": self.rel_path,
            "local_mtime_ns": self.local_mtime_ns,
            "remote_mtime_ns": self.remote_mtime_ns,
            "local_sha256": self.local_sha256,
            "remote_sha256": self.remote_sha256,
            "peer_id": self.peer_id,
            "detected_at": self.detected_at,
            "resolution": self.resolution,
        }


# ── 排除规则 ──────────────────────────────────────────────────────────────────


# vault 内不该 sync 的: 密钥, P2P 状态, daemon pid 等
EXCLUDE_PREFIXES: tuple[str, ...] = (
    "seed.txt",
    "p2p/",
    ".lock",
    ".tmp",
)


def _should_sync(rel_path: str) -> bool:
    """排除 seed.txt / p2p/ 状态 / tmp 文件 (这些是本机 secret 或运行时态)."""
    for prefix in EXCLUDE_PREFIXES:
        if rel_path == prefix or rel_path.startswith(prefix):
            return False
    return True


# ── inventory 构建 ────────────────────────────────────────────────────────────


def build_inventory(vault_root: Path) -> Inventory:
    """遍历 vault root 建 inventory.

    Args:
        vault_root: vault 根目录 (含 dna.json / preferences/ / goals/ ...).

    Returns:
        Inventory 实例 (空 vault 返空 Inventory).
    """
    root = Path(vault_root)
    inv = Inventory()
    if not root.exists():
        return inv

    for f in root.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(root).as_posix()
        if not _should_sync(rel):
            continue
        try:
            st = f.stat()
            data = f.read_bytes()
        except OSError as e:
            log.warning("inventory skip %s: %s", rel, e)
            continue
        sha = hashlib.sha256(data).hexdigest()
        inv.files[rel] = FileMeta(
            rel_path=rel,
            mtime_ns=st.st_mtime_ns,
            size=st.st_size,
            sha256_hex=sha,
        )
    return inv


# ── diff 算法 ────────────────────────────────────────────────────────────────


def compute_diff(local: Inventory, remote: Inventory) -> SyncDiff:
    """对比 local vs remote, 算出 pull / push / conflict.

    规则:
    - remote 有 local 无 → pull
    - local 有 remote 无 → push
    - 两边都有, sha256 同 → 跳过
    - 两边都有, sha256 不同:
      * mtime 差 >= CONFLICT_WINDOW_NS → newer wins (mtime 大的一方为 truth)
      * mtime 差 < CONFLICT_WINDOW_NS → conflict (人工)
    """
    diff = SyncDiff()
    all_paths = set(local.files.keys()) | set(remote.files.keys())
    for rel in sorted(all_paths):
        lo = local.files.get(rel)
        re = remote.files.get(rel)
        if lo is None and re is not None:
            diff.pull.append(rel)
        elif lo is not None and re is None:
            diff.push.append(rel)
        elif lo is not None and re is not None:
            if lo.sha256_hex == re.sha256_hex:
                continue
            # 内容不同
            delta = abs(lo.mtime_ns - re.mtime_ns)
            if delta < CONFLICT_WINDOW_NS:
                diff.conflicts.append(rel)
            elif re.mtime_ns > lo.mtime_ns:
                diff.pull.append(rel)
            else:
                diff.push.append(rel)
    return diff


# ── 文件原子写 ────────────────────────────────────────────────────────────────


def apply_pull(vault_root: Path, rel_path: str, content: bytes, mtime_ns: Optional[int] = None) -> Path:
    """把 remote 拉来的 content 落盘 (原子 temp + rename).

    Args:
        vault_root: vault root.
        rel_path: 相对路径.
        content: 文件内容.
        mtime_ns: 设置 mtime (默认 now); 用 remote mtime 保持 newer-wins 一致.

    Returns:
        实际写入路径.
    """
    target = Path(vault_root) / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp_p2p")
    tmp.write_bytes(content)
    os.replace(tmp, target)
    if mtime_ns is not None:
        try:
            os.utime(target, ns=(mtime_ns, mtime_ns))
        except OSError as e:
            log.warning("set mtime 失败 %s: %s", target, e)
    return target


def record_conflict(
    vault_root: Path,
    rel_path: str,
    local: FileMeta,
    remote: FileMeta,
    peer_id: str,
    remote_content: bytes,
) -> Path:
    """冲突写盘: ``<original>.conflict-<peer>-<mtime>`` 保留 remote 副本 + 写 conflict log.

    Returns:
        写出的 conflict 副本路径.
    """
    short_peer = peer_id[:8] if peer_id else "unknown"
    conflict_name = f"{rel_path}.conflict-{short_peer}-{remote.mtime_ns}"
    conflict_path = Path(vault_root) / conflict_name
    conflict_path.parent.mkdir(parents=True, exist_ok=True)
    conflict_path.write_bytes(remote_content)
    try:
        os.utime(conflict_path, ns=(remote.mtime_ns, remote.mtime_ns))
    except OSError:
        pass

    # 追加 conflict log
    log_path = Path(vault_root) / "p2p" / "conflicts.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    record = ConflictRecord(
        rel_path=rel_path,
        local_mtime_ns=local.mtime_ns,
        remote_mtime_ns=remote.mtime_ns,
        local_sha256=local.sha256_hex,
        remote_sha256=remote.sha256_hex,
        peer_id=peer_id,
    )
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
    return conflict_path


# ── 协议 message 编解码 ───────────────────────────────────────────────────────


def encode_message(msg_type: str, payload: dict) -> bytes:
    """编 JSON message."""
    obj = {"type": msg_type, "payload": payload, "ts": time.time()}
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def decode_message(raw: bytes) -> tuple[str, dict]:
    """解 JSON message → (type, payload)."""
    obj = json.loads(raw.decode("utf-8"))
    return obj["type"], obj.get("payload", {})


__all__ = [
    "CONFLICT_WINDOW_NS",
    "ConflictRecord",
    "EXCLUDE_PREFIXES",
    "FileMeta",
    "Inventory",
    "SyncDiff",
    "apply_pull",
    "build_inventory",
    "compute_diff",
    "decode_message",
    "encode_message",
    "record_conflict",
]
