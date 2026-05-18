"""sisoul vault · 文件 layer (Phase 1 W3).

read/write/list 文件 + 计算 vault 大小. 不做加密 (加密在 encryption.py).
所有路径**必须**显式传入, 不默认 ~/.sisoul/ (防 test 误写真 vault).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

DEFAULT_VAULT_DIR = Path.home() / ".sisoul"


@dataclass(frozen=True)
class VaultPaths:
    """vault dir 内常用子路径. 实例化时绑 root."""

    root: Path

    @property
    def dna(self) -> Path:
        return self.root / "dna.json"

    @property
    def preferences_dir(self) -> Path:
        return self.root / "preferences"

    @property
    def goals_dir(self) -> Path:
        return self.root / "goals"

    @property
    def chat_history_dir(self) -> Path:
        return self.root / "chat-history"

    def ensure_dirs(self) -> None:
        """建 vault 全部子 dir (mkdir -p 语义)."""
        for d in (self.root, self.preferences_dir, self.goals_dir, self.chat_history_dir):
            d.mkdir(parents=True, exist_ok=True)


def read_file(path: Path) -> str:
    """读 utf-8 文件. 不存在抛 FileNotFoundError."""
    return Path(path).read_text(encoding="utf-8")


def write_file(path: Path, content: str, *, mkdir: bool = True) -> Path:
    """写 utf-8 文件. mkdir=True 时自动建父 dir."""
    p = Path(path)
    if mkdir:
        p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def list_files(dir_path: Path, pattern: str = "*.md") -> list[Path]:
    """list dir 下匹配 pattern 的文件 (sorted by name).

    dir 不存在 → 返回空 list (不抛错).
    pattern 默认 *.md (vault 主流).
    """
    p = Path(dir_path)
    if not p.exists():
        return []
    return sorted(p.glob(pattern))


def vault_size(root: Path) -> int:
    """递归统计 vault 总字节. 不存在 → 0."""
    p = Path(root)
    if not p.exists():
        return 0
    total = 0
    for f in _walk_files(p):
        try:
            total += f.stat().st_size
        except OSError:
            # 文件突然消失 / 权限 → 忽略
            continue
    return total


def _walk_files(root: Path) -> Iterator[Path]:
    """递归 yield 所有文件 (不含 dir)."""
    for child in root.rglob("*"):
        if child.is_file():
            yield child
