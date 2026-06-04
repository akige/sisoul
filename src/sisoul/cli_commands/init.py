"""sisoul init 命令 (Phase 1 W3 + Phase 2 W17-W20 加 BIP-39 seed).

引导建本地 vault:
1. 检查 vault_dir (默认 ~/.sisoul/) 是否已存在 → 存在 abort, 除非 --force
2. 生成 / 导入 BIP-39 12 词 seed (除非 --skip-seed)
   - 默认: 生成新 seed 写 <vault_dir>/seed.txt + chmod 600 + 终端打印
   - --import-seed "<12 words>": 用已有 seed
3. 引导填 1-3 个长期目标 (交互 typer.prompt 或 --goals 'a,b,c' 自动化)
4. 写 dna.json (sisoul_version / vault_created_at / master_key_hash / has_seed)
5. 写 goals/<id>.md
6. 建 preferences/ + chat-history/ 空 dir

签名: init(goals, force, vault_dir, skip_seed, import_seed)
    skip_seed: True → 不生成/导入 seed (单元测试用; vault 仍走 fallback master_key)
    import_seed: "<mnemonic>" → 不生成新 seed, 用传入的合法 BIP-39
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import typer

from sisoul import __version__
from sisoul.identity import (
    InvalidMnemonicError,
    generate_mnemonic,
    mnemonic_to_master_key,
    save_mnemonic_to_file,
    verify_mnemonic,
)
from sisoul.identity import derive_subkey as _derive_subkey
from sisoul.vault import (
    DEFAULT_VAULT_DIR,
    VaultPaths,
    derive_master_key,
    dump_frontmatter,
    write_file,
)

# 上限 (产品决策, §28 §1.1 装机引导 1-3 个长期目标)
MAX_GOALS = 3
MIN_GOALS = 1

# vault 内 seed 文件名 (跟 ~/.sisoul/seed.txt 默认一致)
SEED_FILENAME = "seed.txt"

# vault subkey purpose (跟 vault.encryption._VAULT_PURPOSE 一致)
_VAULT_PURPOSE = "vault"


class InitAbort(Exception):
    """vault 已存在且未 --force → abort, CLI 转 typer.Exit(1)."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _goal_id(index: int) -> str:
    """index 1-based → 'goal-001'."""
    return f"goal-{index:03d}"


def _write_dna(paths: VaultPaths, master_key_hash: str, has_seed: bool) -> None:
    dna = {
        "sisoul_version": __version__,
        "vault_created_at": _now_iso(),
        "master_key_hash": master_key_hash,
        "has_seed": has_seed,
        "phase": "Phase 2 W17-W20 (BIP-39 灵魂迁移)",
        "schema_version": 2,  # bump: 加 has_seed
    }
    write_file(paths.dna, json.dumps(dna, indent=2, ensure_ascii=False) + "\n")


def _write_goal(paths: VaultPaths, idx: int, title: str) -> Path:
    gid = _goal_id(idx)
    meta = {
        "id": gid,
        "title": title,
        "created_at": _now_iso(),
        "progress": 0,
        "status": "active",
    }
    body = f"# {title}\n\n_长期目标. 进度 0/100. 由 sisoul init 引导填写._\n"
    return write_file(paths.goals_dir / f"{gid}.md", dump_frontmatter(meta, body))


def _validate_goals(goals_list: list[str]) -> list[str]:
    cleaned = [g.strip() for g in goals_list if g.strip()]
    if not (MIN_GOALS <= len(cleaned) <= MAX_GOALS):
        raise typer.BadParameter(
            f"必须填 {MIN_GOALS}-{MAX_GOALS} 个长期目标, 实际 {len(cleaned)} 个"
        )
    return cleaned


def _prompt_goals_interactive() -> list[str]:
    """交互式收集 1-3 个目标. typer.prompt 直到达到 MIN_GOALS, 满 MAX_GOALS 自动停."""
    typer.echo(f"请输入 {MIN_GOALS}-{MAX_GOALS} 个长期目标 (空行结束):")
    out: list[str] = []
    for i in range(1, MAX_GOALS + 1):
        prompt_msg = f"  目标 {i}"
        default = "" if i > MIN_GOALS else None
        try:
            ans = typer.prompt(prompt_msg, default=default, show_default=False)
        except typer.Abort:  # Ctrl+C
            raise
        ans = ans.strip()
        if not ans:
            if i <= MIN_GOALS:
                raise typer.BadParameter("第 1 个目标必填")
            break
        out.append(ans)
    return out


def _handle_seed(
    paths: VaultPaths,
    skip_seed: bool,
    import_seed: str | None,
) -> tuple[bytes, bool]:
    """生成 / 导入 / 跳过 seed.

    Returns:
        (master_key_32B, has_seed_bool)

    Raises:
        InvalidMnemonicError: --import-seed 传了非法 mnemonic.
    """
    if skip_seed:
        master_key = derive_master_key()  # fallback (placeholder + sha256)
        return master_key, False

    seed_path = paths.root / SEED_FILENAME

    if import_seed is not None:
        mnemonic = import_seed.strip()
        if not verify_mnemonic(mnemonic):
            raise InvalidMnemonicError(
                f"--import-seed 不是合法 BIP-39 mnemonic (词数 / checksum 错): "
                f"{mnemonic[:40]}..."
            )
        typer.echo(f"📥 导入已有 BIP-39 seed ({len(mnemonic.split())} 词)")
    else:
        mnemonic = generate_mnemonic(strength=128)
        typer.echo("")
        typer.echo("🔑 已生成 BIP-39 12 词 seed (灵魂迁移 master key):")
        typer.echo("")
        # 4x3 排版方便手抄
        words = mnemonic.split()
        for row_start in range(0, len(words), 4):
            row = words[row_start : row_start + 4]
            line = "    " + "  ".join(f"{row_start + i + 1:2d}. {w:10s}"
                                       for i, w in enumerate(row))
            typer.echo(line)
        typer.echo("")
        typer.echo("⚠️  立即截图 / 手抄保存到离线安全位置.")
        typer.echo("⚠️  丢 seed = 永远无法恢复 vault (零 backdoor).")
        typer.echo("")

    # 写 seed 文件 (chmod 600)
    if seed_path.exists():
        # --force 走 init 时可能 vault 复用; 删旧再写 (我们持有新 mnemonic, 旧的可丢)
        seed_path.unlink()
    actual_path = save_mnemonic_to_file(mnemonic, seed_path)
    typer.echo(f"💾 seed 已写: {actual_path} (chmod 600)")

    # 派生 vault master key (跟 encryption._VAULT_PURPOSE 一致)
    master_seed = mnemonic_to_master_key(mnemonic)
    master_key = _derive_subkey(master_seed, _VAULT_PURPOSE, index=0)
    return master_key, True


def run_init(
    goals: str | None = None,
    force: bool = False,
    vault_dir: Path | None = None,
    interactive: bool = True,
    skip_seed: bool = False,
    import_seed: str | None = None,
) -> VaultPaths:
    """init 主逻辑. 返回 VaultPaths (调用方可继续操作).

    raises:
        InitAbort: vault 已存在且未 force.
        typer.BadParameter: goals 数不合规.
        InvalidMnemonicError: --import-seed 不合法.
    """
    root = Path(vault_dir) if vault_dir is not None else DEFAULT_VAULT_DIR
    paths = VaultPaths(root=root)

    if root.exists() and any(root.iterdir()):
        if not force:
            raise InitAbort(f"vault 已存在: {root} (用 --force 覆盖)")
        typer.echo(f"⚠️  --force: 已存在 vault, 将覆写新文件 (不删旧): {root}")

    paths.ensure_dirs()

    if goals:
        goals_list = _validate_goals(goals.split(","))
    elif interactive:
        goals_list = _prompt_goals_interactive()
    else:
        raise typer.BadParameter("非交互模式必须传 --goals 'a,b,c'")

    # BIP-39 seed (Phase 2 W17-W20)
    master_key, has_seed = _handle_seed(paths, skip_seed=skip_seed, import_seed=import_seed)
    master_key_hash = hashlib.sha256(master_key).hexdigest()[:16]
    _write_dna(paths, master_key_hash, has_seed=has_seed)

    for i, title in enumerate(goals_list, start=1):
        _write_goal(paths, i, title)

    typer.echo("")
    typer.echo(f"✅ sisoul vault 已建: {root}")
    typer.echo(f"   dna.json + {len(goals_list)} 个长期目标 + preferences/ + chat-history/")
    if has_seed:
        typer.echo(f"   seed: {paths.root / SEED_FILENAME} (BIP-39 12 词, chmod 600)")
    else:
        typer.echo("   ⚠️  无 seed (--skip-seed), 仅 dev/test 用")
    typer.echo("")
    typer.echo("Next:")
    typer.echo("  sisoul login --provider claude     # 接 LLM provider key")
    typer.echo("  sisoul remember '我用 Tailwind'    # 教偏好")
    typer.echo("  sisoul status                      # 看 vault 状态")
    if has_seed:
        typer.echo("  # 别机恢复: sisoul restore <12 words> --vault-dir <path>")
    return paths


# ── P2-EF 首启 wizard ────────────────────────────────────────────────────────

# wizard 5 步:
#   1) Petname  (默认 socket.gethostname())
#   2) 自动生 did:key, 显前 8 + 后 4
#   3) LLM provider 选 (default / custom / skip)
#   4) daemon 模式 (background / foreground)
#   5) 出 QR + 提示给朋友扫
#
# --non-interactive 模式从 env 读取:
#   SISOUL_INIT_PETNAME     默认 hostname
#   SISOUL_INIT_PROVIDER    default | custom:<name> | skip   (默认 skip)
#   SISOUL_INIT_DAEMON      background | foreground          (默认 background)
#   SISOUL_INIT_QR          1 输出 PNG, 0 跳过 (默认 0)
#   SISOUL_INIT_QR_OUT      QR PNG 路径 (默认 <vault>/qr.png)

import os
import socket


def _default_petname() -> str:
    try:
        return socket.gethostname() or "sisoul-user"
    except Exception:  # noqa: BLE001
        return "sisoul-user"


def _wizard_step_petname(non_interactive: bool) -> str:
    default = os.environ.get("SISOUL_INIT_PETNAME", _default_petname())
    if non_interactive:
        typer.echo(f"[1/5] petname = {default} (from env / hostname)")
        return default
    typer.echo("[1/5] 起个 petname (本地昵称, 朋友会看到):")
    return typer.prompt("  petname", default=default)


def _wizard_step_did(vault_dir: Path) -> str:
    """生成临时 did:key 给用户看; 真实 did 由 _handle_seed 派生 (后续步骤)."""
    from sisoul.identity import generate_did_key

    placeholder_seed = os.urandom(32)
    did = generate_did_key(placeholder_seed)
    short = f"{did[:12]}...{did[-4:]}"
    typer.echo(f"[2/5] 已自动生成 did:key (预览, 真 did 由 BIP-39 seed 派生)")
    typer.echo(f"      {short}")
    return did


def _wizard_step_provider(non_interactive: bool) -> str:
    raw = os.environ.get("SISOUL_INIT_PROVIDER", "skip")
    valid_starts = ("default", "custom", "skip")
    if non_interactive:
        if not raw.startswith(valid_starts):
            typer.echo(f"WARN: SISOUL_INIT_PROVIDER={raw!r} 非法, 用 skip", err=True)
            raw = "skip"
        typer.echo(f"[3/5] LLM provider = {raw}")
        return raw
    typer.echo("[3/5] LLM provider 选 (default / custom / skip):")
    choice = typer.prompt("  provider", default="default")
    if not choice.startswith(valid_starts):
        typer.echo(f"WARN: {choice!r} 非法 (default/custom/skip), 用 skip", err=True)
        choice = "skip"
    return choice


def _wizard_step_daemon(non_interactive: bool) -> str:
    raw = os.environ.get("SISOUL_INIT_DAEMON", "background")
    if raw not in ("background", "foreground"):
        raw = "background"
    if non_interactive:
        typer.echo(f"[4/5] daemon 模式 = {raw}")
        return raw
    typer.echo("[4/5] daemon 模式 (background / foreground):")
    choice = typer.prompt("  daemon", default="background")
    if choice not in ("background", "foreground"):
        typer.echo(f"WARN: {choice!r} 非法, 用 background", err=True)
        choice = "background"
    return choice


def _wizard_step_qr(
    did: str, petname: str, vault_dir: Path, non_interactive: bool
) -> Optional[Path]:
    want_qr_env = os.environ.get("SISOUL_INIT_QR", "1") not in ("0", "false", "no")
    qr_out_env = os.environ.get("SISOUL_INIT_QR_OUT", "")
    if non_interactive:
        if not want_qr_env:
            typer.echo("[5/5] 跳过 QR (SISOUL_INIT_QR=0)")
            return None
    else:
        ans = typer.prompt("[5/5] 生 QR PNG 给朋友扫? (y/n)", default="y")
        if ans.strip().lower() not in ("y", "yes", "1", "true"):
            typer.echo("      跳过 QR.")
            return None

    out_path = Path(qr_out_env).expanduser() if qr_out_env else vault_dir / "qr.png"
    try:
        from sisoul.cli_commands.qr import build_payload, generate_qr_png

        payload = build_payload(did=did, multiaddr="", petname_hint=petname)
        generate_qr_png(payload, out_path)
        typer.echo(f"      QR 已写: {out_path}")
        typer.echo("      把 PNG 发给朋友, ta 跑: sisoul friend qr-scan <png>")
        return out_path
    except Exception as e:  # noqa: BLE001
        typer.echo(f"      WARN: 生 QR 失败 ({e})", err=True)
        return None


# 让 Optional 在文件顶可见
from typing import Optional  # noqa: E402


def run_wizard(
    vault_dir: Optional[Path] = None,
    non_interactive: bool = False,
    skip_seed: bool = True,
) -> dict:
    """首启 5 步 wizard. 返回收集到的 config dict (不真起 daemon)."""
    root = Path(vault_dir) if vault_dir is not None else DEFAULT_VAULT_DIR
    root.mkdir(parents=True, exist_ok=True)

    typer.echo("")
    typer.echo("=== sisoul 首启 wizard (5 步) ===")
    petname = _wizard_step_petname(non_interactive)
    did = _wizard_step_did(root)
    provider = _wizard_step_provider(non_interactive)
    daemon_mode = _wizard_step_daemon(non_interactive)
    qr_path = _wizard_step_qr(did, petname, root, non_interactive)

    config = {
        "petname": petname,
        "did_preview": did,
        "provider": provider,
        "daemon_mode": daemon_mode,
        "qr_path": str(qr_path) if qr_path else None,
        "vault_dir": str(root),
    }
    config_path = root / "wizard.json"
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    typer.echo("")
    typer.echo(f"✅ wizard 完成, 写入 {config_path}")
    typer.echo(f"   petname={petname} provider={provider} daemon={daemon_mode}")
    return config


# typer command 包装 (cli.py 整合用)
def cli_init(
    goals: str = typer.Option(
        None,
        "--goals",
        help="逗号分隔 1-3 个长期目标 (非交互, 例: --goals 'a,b,c')",
    ),
    force: bool = typer.Option(False, "--force", help="覆盖已存在 vault"),
    vault_dir: Path = typer.Option(
        None,
        "--vault-dir",
        help="vault 路径 (默认 ~/.sisoul/, 单元测试用)",
    ),
    skip_seed: bool = typer.Option(
        False,
        "--skip-seed",
        help="跳过 BIP-39 seed 生成 (仅 dev/test, 真用户不要用)",
    ),
    import_seed: str = typer.Option(
        None,
        "--import-seed",
        help="用已有 BIP-39 mnemonic 创建 vault (跨设备恢复语义之外, 直接 init)",
    ),
    wizard: bool = typer.Option(
        False, "--wizard", help="首启 5 步 wizard (petname/did/provider/daemon/QR)"
    ),
    non_interactive: bool = typer.Option(
        False,
        "--non-interactive",
        help="非交互 (env: SISOUL_INIT_PETNAME / _PROVIDER / _DAEMON / _QR / _QR_OUT)",
    ),
) -> None:
    """引导建本地 vault + 长期目标 + BIP-39 seed (Phase 2 W17-W20)."""
    if wizard:
        try:
            run_wizard(vault_dir=vault_dir, non_interactive=non_interactive)
        except typer.Exit:
            raise
        except Exception as e:  # noqa: BLE001
            typer.echo(f"❌ wizard 失败: {e}", err=True)
            raise typer.Exit(code=3)
        return

    try:
        run_init(
            goals=goals,
            force=force,
            vault_dir=vault_dir,
            interactive=goals is None,
            skip_seed=skip_seed,
            import_seed=import_seed,
        )
    except InitAbort as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=1)
    except InvalidMnemonicError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=2)
