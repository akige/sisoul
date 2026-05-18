"""sisoul restore 命令 (Phase 1 W13 ZIP + Phase 2 W17-W20 BIP-39 seed).

三种用法:
  1. sisoul restore --from-zip <path>            (ZIP restore, Phase 1)
  2. sisoul restore <12-words>                   (BIP-39 seed restore, Phase 2)
  3. sisoul restore --from-seed-file <path>      (BIP-39 seed restore from file)

ZIP 路径 = 包含完整 vault 内容 (含 preferences/goals/chat-history) + dna.json.
BIP-39 seed 路径 = 只能恢复 master_key (派生身份), vault 内容需重新生成 / 后续 sync 补.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import typer

from sisoul import __version__
from sisoul.identity import (
    InvalidMnemonicError,
    derive_subkey,
    load_mnemonic_from_file,
    mnemonic_to_master_key,
    save_mnemonic_to_file,
    verify_mnemonic,
)
from sisoul.vault import (
    DEFAULT_VAULT_DIR,
    VaultPaths,
    decrypt_bytes,
    encrypt_bytes,
)

# vault subkey purpose (跟 encryption._VAULT_PURPOSE 一致)
_VAULT_PURPOSE = "vault"

# vault 内 seed 文件名 (跟 init.py SEED_FILENAME 一致)
SEED_FILENAME = "seed.txt"

# dna.json 必含字段
DNA_REQUIRED_FIELDS = {"sisoul_version", "vault_created_at"}


class RestoreError(Exception):
    """restore 失败的通用异常."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validate_dna(dna_path: Path) -> dict:
    """读 dna.json 并验证必含字段."""
    if not dna_path.exists():
        raise RestoreError(f"dna.json 缺失: {dna_path}")
    try:
        text = dna_path.read_text(encoding="utf-8")
        dna = json.loads(text)
    except (OSError, json.JSONDecodeError) as e:
        raise RestoreError(f"dna.json 解析失败: {e}") from e
    missing = DNA_REQUIRED_FIELDS - set(dna.keys())
    if missing:
        raise RestoreError(f"dna.json 缺字段: {missing}")
    return dna


def _list_vault_entries(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """取 ZIP 里 vault/ 前缀的所有条目."""
    return [
        info for info in zf.infolist()
        if info.filename.startswith("vault/") and not info.filename.endswith("/")
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 W13: ZIP restore
# ─────────────────────────────────────────────────────────────────────────────


def run_restore(
    zip_path: Path,
    vault_dir: Path | None = None,
    force: bool = False,
) -> VaultPaths:
    """ZIP restore 主逻辑 (波 2 dev-D ship, 保持原行为)."""
    zip_path = Path(zip_path).expanduser().resolve()
    root = Path(vault_dir) if vault_dir is not None else DEFAULT_VAULT_DIR
    paths = VaultPaths(root=root)

    if not zip_path.exists():
        typer.echo(f"❌ ZIP 文件不存在: {zip_path}", err=True)
        raise SystemExit(1)
    if not zipfile.is_zipfile(zip_path):
        typer.echo(f"❌ 不是有效 ZIP: {zip_path}", err=True)
        raise SystemExit(1)

    if root.exists() and any(root.iterdir()):
        if not force:
            typer.echo(f"❌ vault 已存在: {root}", err=True)
            typer.echo("  用 --force 覆盖", err=True)
            raise SystemExit(1)
        typer.echo(f"⚠️  --force: 已存在 vault, 将覆写冲突文件: {root}")

    paths.ensure_dirs()

    file_count = 0
    with zipfile.ZipFile(zip_path, "r") as zf:
        vault_entries = _list_vault_entries(zf)
        if not vault_entries:
            typer.echo("❌ ZIP 内无 vault/ 目录 (不是 sisoul export 生成的 ZIP?)", err=True)
            raise SystemExit(1)
        for info in vault_entries:
            rel_str = info.filename[len("vault/") :]
            if not rel_str:
                continue
            dest = root / rel_str
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(info.filename))
            file_count += 1

    try:
        dna = _validate_dna(paths.dna)
    except RestoreError as e:
        typer.echo(f"❌ vault 验证失败: {e}", err=True)
        typer.echo("  ZIP 可能已损坏或不完整", err=True)
        raise SystemExit(1)

    typer.echo(f"✅ restored from ZIP: {zip_path}")
    typer.echo(f"   vault: {root}")
    typer.echo(f"   文件数: {file_count}")
    typer.echo(f"   sisoul_version: {dna.get('sisoul_version', 'unknown')}")
    typer.echo(f"   vault_created_at: {dna.get('vault_created_at', 'unknown')}")
    typer.echo("")
    typer.echo("Next:")
    typer.echo("  sisoul status                     # 验证 vault 状态")
    typer.echo("  sisoul login --provider claude    # 重新接 LLM key")
    return paths


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 W17-W20: BIP-39 seed restore (本波 ship)
# ─────────────────────────────────────────────────────────────────────────────


def _read_seed_input(seed: str | None, from_seed_file: Path | None) -> str:
    """读 mnemonic 输入 (CLI string 或 文件)."""
    if seed and from_seed_file:
        raise RestoreError("--from-seed-file 与位置参数 seed 二选一")
    if from_seed_file:
        return load_mnemonic_from_file(from_seed_file)
    if seed:
        return seed.strip()
    raise RestoreError("必须传 seed (位置参数) 或 --from-seed-file <path>")


def run_restore_from_seed(
    seed: str | None = None,
    from_seed_file: Path | None = None,
    vault_dir: Path | None = None,
    force: bool = False,
) -> VaultPaths:
    """BIP-39 seed restore 主逻辑.

    流程:
    1. 拿 mnemonic (string 或 file)
    2. verify_mnemonic 校验 checksum
    3. 派生 master_seed → vault subkey
    4. 建 vault dir (若已存在: --force 才覆盖)
    5. 写 dna.json (含 master_key_hash + restored_from_seed flag)
    6. 写 seed.txt 到 vault dir (chmod 600)
    7. 自检: 用派生 key 加密 + 解密 test payload, 确认可加载

    Returns:
        VaultPaths

    Raises:
        InvalidMnemonicError: mnemonic 非法
        RestoreError: 其他失败
        SystemExit(1): vault 已存在且无 --force
    """
    mnemonic = _read_seed_input(seed, from_seed_file)
    if not verify_mnemonic(mnemonic):
        raise InvalidMnemonicError(
            f"BIP-39 mnemonic 校验失败 (checksum / 词表错): "
            f"{mnemonic[:40]}..."
        )

    root = Path(vault_dir) if vault_dir is not None else DEFAULT_VAULT_DIR
    paths = VaultPaths(root=root)

    if root.exists() and any(root.iterdir()):
        if not force:
            typer.echo(f"❌ vault 已存在: {root}", err=True)
            typer.echo("  用 --force 覆盖", err=True)
            raise SystemExit(1)
        typer.echo(f"⚠️  --force: 已存在 vault, 将覆写: {root}")
        # 清掉旧 seed (新 seed 接管)
        old_seed = paths.root / SEED_FILENAME
        if old_seed.exists():
            old_seed.unlink()

    paths.ensure_dirs()

    # 派生 key
    master_seed = mnemonic_to_master_key(mnemonic)
    vault_key = derive_subkey(master_seed, _VAULT_PURPOSE, index=0)
    master_key_hash = hashlib.sha256(vault_key).hexdigest()[:16]

    # 自检: 加密 + 解密
    test_payload = b"sisoul-restore-selftest"
    blob = encrypt_bytes(test_payload, vault_key)
    if decrypt_bytes(blob, vault_key) != test_payload:  # pragma: no cover · 库 bug
        raise RestoreError("自检失败: 派生 key 加解密 roundtrip 不一致")

    # 写 seed 到 vault dir
    seed_path = paths.root / SEED_FILENAME
    save_mnemonic_to_file(mnemonic, seed_path)

    # 写 dna.json (新 vault, 标记 restored_from_seed)
    dna = {
        "sisoul_version": __version__,
        "vault_created_at": _now_iso(),
        "master_key_hash": master_key_hash,
        "has_seed": True,
        "restored_from_seed": True,
        "phase": "Phase 2 W17-W20 (BIP-39 seed restore)",
        "schema_version": 2,
    }
    paths.dna.write_text(
        json.dumps(dna, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    typer.echo("✅ restored from BIP-39 seed")
    typer.echo(f"   vault: {root}")
    typer.echo(f"   master_key_hash: {master_key_hash}")
    typer.echo(f"   seed 保存: {seed_path} (chmod 600)")
    typer.echo("")
    typer.echo("⚠️  此恢复仅重建 master_key + dna.json + 空 vault 结构.")
    typer.echo("⚠️  原 vault 内容 (preferences / goals / chat-history) 需:")
    typer.echo("    - sisoul restore --from-zip <path>  (若有 export ZIP)")
    typer.echo("    - 或后续 P2P sync 从朋友 / 别设备拉")
    typer.echo("")
    typer.echo("Next:")
    typer.echo("  sisoul status --vault-dir {0}     # 验证 vault".format(root))
    typer.echo("  sisoul login --provider claude    # 重新接 LLM key (key 不随 seed 恢复)")
    return paths


# ─────────────────────────────────────────────────────────────────────────────
# typer command 包装 (cli.py 整合用)
# ─────────────────────────────────────────────────────────────────────────────


def cli_restore(
    seed: str = typer.Argument(
        None,
        help="BIP-39 12 词 seed (Phase 2, 与 --from-zip / --from-seed-file 三选一)",
    ),
    from_zip: Path = typer.Option(
        None, "--from-zip", help="ZIP 文件路径 (Phase 1 W13 路径)"
    ),
    from_seed_file: Path = typer.Option(
        None, "--from-seed-file", help="BIP-39 seed 文件路径 (Phase 2)"
    ),
    vault_dir: Path = typer.Option(
        None, "--vault-dir", help="还原目标路径 (默认 ~/.sisoul/, 单元测试用)"
    ),
    force: bool = typer.Option(
        False, "--force", help="vault 已存在时强制覆盖"
    ),
) -> None:
    """从 ZIP 还原 vault 或从 BIP-39 seed 跨设备恢复身份."""
    # 路径优先级: --from-zip > --from-seed-file > seed 位置参数
    if from_zip:
        run_restore(zip_path=from_zip, vault_dir=vault_dir, force=force)
        return
    if from_seed_file or seed:
        try:
            run_restore_from_seed(
                seed=seed,
                from_seed_file=from_seed_file,
                vault_dir=vault_dir,
                force=force,
            )
        except InvalidMnemonicError as e:
            typer.echo(f"❌ {e}", err=True)
            raise typer.Exit(code=2)
        except RestoreError as e:
            typer.echo(f"❌ {e}", err=True)
            raise typer.Exit(code=1)
        return
    typer.echo(
        "❌ 必须传 ZIP (--from-zip), seed file (--from-seed-file), 或 12 词 seed 作为位置参数",
        err=True,
    )
    raise typer.Exit(code=1)
