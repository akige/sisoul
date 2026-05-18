"""sisoul export 命令 (Phase 1 W13).

`sisoul export [--output PATH]`

功能:
- 默认 PATH: ~/sisoul-export-<YYYY-MM-DD-HHMM>.zip
- ZIP 内容:
    - vault 整个 (~/.sisoul/)
    - dna.json (已含在 vault, 单独也列出来 for clarity)
    - README-export.md (装机指引: "在新机跑 sisoul init --import <zip>")
- 输出: ZIP 路径 + size (human-readable) + 文件数

设计原则:
- 不加密 ZIP 本身 (vault 文件已有 libsodium 加密, Phase 1 W4 完成后)
- 不含 .venv / __pycache__ / *.pyc (只含 vault 数据)
- vault_dir 参数供测试用 (默认 DEFAULT_VAULT_DIR)
"""

from __future__ import annotations

import zipfile
from datetime import datetime, timezone
from pathlib import Path

import typer

from sisoul.vault import DEFAULT_VAULT_DIR, VaultPaths

# 写入 ZIP 的 README 文件名
README_EXPORT_NAME = "README-export.md"

# 写入 ZIP 的 README 内容
README_EXPORT_CONTENT = """\
# sisoul vault export

这是 sisoul vault 的完整导出包.

## 在新机还原

```bash
# 方式 1 (推荐): init 时直接 import
sisoul init --import /path/to/this.zip

# 方式 2: restore 子命令
sisoul restore --from-zip /path/to/this.zip
```

还原后:
- vault 会写入 ~/.sisoul/
- dna.json 完整性会被验证
- 如果 vault 已存在, 用 --force 覆盖

## vault 结构

```
~/.sisoul/
  dna.json            — 元数据 (版本 / 创建时间 / master key hash)
  preferences/        — 偏好库 (markdown)
  goals/              — 长期目标 (markdown, frontmatter)
  chat-history/       — chat 历史 (Phase 1 W11+)
```

## 注意

- Phase 2 (W19-W20) 支持 BIP-39 12 词 seed 恢复 (跨设备灵魂迁移)
- 本 ZIP 是 W13 简版 ZIP 恢复, 不含 seed
"""

# 不打进 ZIP 的 glob 模式
_EXCLUDE_PATTERNS = {
    ".venv",
    "__pycache__",
    ".git",
    "*.pyc",
    "*.pyo",
    ".DS_Store",
    "Thumbs.db",
}


def _human_size(size_bytes: int) -> str:
    """字节数 → 人类可读字符串 (KB / MB / GB)."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 ** 3:
        return f"{size_bytes / 1024 ** 2:.1f} MB"
    else:
        return f"{size_bytes / 1024 ** 3:.2f} GB"


def _default_output_path() -> Path:
    """默认输出路径: ~/sisoul-export-<YYYY-MM-DD-HHMM>.zip"""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")
    return Path.home() / f"sisoul-export-{ts}.zip"


def _should_exclude(rel_path: str) -> bool:
    """判断相对路径是否要排除出 ZIP."""
    parts = Path(rel_path).parts
    for part in parts:
        if part in _EXCLUDE_PATTERNS:
            return True
        for pat in _EXCLUDE_PATTERNS:
            if pat.startswith("*") and part.endswith(pat[1:]):
                return True
    return False


def run_export(
    output: Path | None = None,
    vault_dir: Path | None = None,
) -> Path:
    """export 主逻辑.

    Args:
        output: ZIP 输出路径. None → 默认时间戳路径.
        vault_dir: vault 根目录. None → DEFAULT_VAULT_DIR.

    Returns:
        生成的 ZIP 文件路径.

    Raises:
        typer.Exit(1): vault 不存在或 dna.json 缺失.
    """
    root = Path(vault_dir) if vault_dir is not None else DEFAULT_VAULT_DIR
    paths = VaultPaths(root=root)

    # 验证 vault 存在
    if not root.exists():
        typer.echo(f"❌ vault 不存在: {root}", err=True)
        typer.echo("  先跑: sisoul init", err=True)
        raise SystemExit(1)

    # 验证 dna.json 存在
    if not paths.dna.exists():
        typer.echo(f"❌ dna.json 缺失: {paths.dna}", err=True)
        typer.echo("  vault 可能已损坏, 先跑: sisoul verify", err=True)
        raise SystemExit(1)

    # 确定输出路径
    out_path = output if output is not None else _default_output_path()
    out_path = Path(out_path).expanduser().resolve()

    # 建 ZIP
    file_count = 0
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # 写 vault 所有文件
        for src_file in sorted(root.rglob("*")):
            if not src_file.is_file():
                continue
            rel = src_file.relative_to(root)
            rel_str = str(rel)
            if _should_exclude(rel_str):
                continue
            # arcname: vault/<rel_path> (保持目录结构, 区别于 README)
            arcname = f"vault/{rel_str}"
            zf.write(src_file, arcname=arcname)
            file_count += 1

        # 写 README-export.md (装机指引)
        zf.writestr(README_EXPORT_NAME, README_EXPORT_CONTENT)
        file_count += 1

    zip_size = out_path.stat().st_size

    typer.echo(f"✅ export 完成: {out_path}")
    typer.echo(f"   大小: {_human_size(zip_size)}  文件数: {file_count}")
    typer.echo("")
    typer.echo("在新机还原:")
    typer.echo(f"  sisoul init --import {out_path}")
    typer.echo(f"  # 或: sisoul restore --from-zip {out_path}")

    return out_path


# typer command 包装 (cli.py 整合用)
def cli_export(
    output: Path = typer.Option(
        None,
        "--output",
        "-o",
        help="ZIP 输出路径 (默认: ~/sisoul-export-<YYYY-MM-DD-HHMM>.zip)",
    ),
    vault_dir: Path = typer.Option(
        None,
        "--vault-dir",
        help="vault 路径 (默认 ~/.sisoul/, 单元测试用)",
    ),
) -> None:
    """一键 ZIP 导出 vault (Phase 1 W13)."""
    run_export(output=output, vault_dir=vault_dir)
