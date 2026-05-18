"""sisoul skill · CLI 子 app (Phase 4 W70-W74 · 波 6 dev-A).

§28 §3.6 AI 技能 share. 6 子命令:

  sisoul skill create <name> [--from-file path]
  sisoul skill list [--owned|--available-to-borrow]
  sisoul skill lend <skill_id> [--max-duration 30]
  sisoul skill borrow <owner_did>:<skill_name> [--duration 30]
  sisoul skill sessions [--mine|--mine-as-borrower]
  sisoul skill end-session <session_id>

接入 cli.py:
    from sisoul.cli_commands.skill import skill_app
    app.add_typer(skill_app, name="skill")
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Optional

import typer

from sisoul.friend.skill_borrow import (
    DEFAULT_BORROW_DURATION_MINUTES,
    SkillBorrowError,
    end_skill_borrow_session,
    get_borrow_session,
    list_borrow_sessions,
    request_borrow_skill,
)
from sisoul.friend.skill_package import (
    DEFAULT_SKILL_EXPIRY_HOURS,
    InvalidSkillPackageError,
    SkillPackage,
    decrypt_skill_package,
    encrypt_skill_package,
    package_skill,
    parse_qualified_name,
)
from sisoul.friend.skill_ipfs import (
    SkillIPFSClient,
    SkillIPFSError,
    SkillPinDB,
)

skill_app = typer.Typer(
    name="skill",
    help="AI 技能 packaging + share (§28 §3.6, 波 6 dev-A).",
    no_args_is_help=True,
)


# ── helper: 本地 owned skills store ────────────────────────────────────────
#
# sisoul skill create 把 SkillPackage 序列化到 ~/.sisoul/skills/owned/<skill_id>.json
# (明文, owner 自己本机, 不加密). lend 时按需加密 + IPFS pin.
# create + lend 分离: create 是先训完打包 → 可多次 lend 给不同朋友.

def _owned_skills_dir() -> Path:
    """lazy 重算 (兼容 monkeypatch HOME for tests)."""
    return Path.home() / ".sisoul" / "skills" / "owned"


def _owned_skill_path(skill_id: str) -> Path:
    d = _owned_skills_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{skill_id}.json"


def _load_owned_skill(skill_id: str) -> SkillPackage:
    p = _owned_skill_path(skill_id)
    if not p.exists():
        raise typer.Exit(code=1)
    return SkillPackage.from_json(p.read_text(encoding="utf-8"))


def _save_owned_skill(pkg: SkillPackage) -> Path:
    p = _owned_skill_path(pkg.skill_id)
    p.write_text(pkg.to_json(), encoding="utf-8")
    return p


def _list_owned_skill_ids() -> list[str]:
    d = _owned_skills_dir()
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.json"))


# ── owner DID helper ────────────────────────────────────────────────────────


def _resolve_own_did(explicit: Optional[str] = None) -> str:
    """优先显式参数, 否则查 ~/.sisoul DID registry, 兜底 'me.local'."""
    if explicit:
        return explicit
    try:
        from sisoul.identity.did import list_local_dids  # type: ignore[import-untyped]
        dids = list_local_dids()
        if dids:
            return dids[0].did_string
    except Exception:
        pass
    return "me.local"


# ── 子命令 ──────────────────────────────────────────────────────────────────


@skill_app.command("create")
def cmd_create(
    name: str = typer.Argument(..., help="skill 名 (e.g. solidity-expert)"),
    from_file: Optional[Path] = typer.Option(
        None, "--from-file", "-f",
        help="读 system prompt 的 markdown 文件 (整文件作 system_prompt)",
    ),
    system_prompt: Optional[str] = typer.Option(
        None, "--system-prompt", "-s",
        help="行内 system prompt (跟 --from-file 二选一)",
    ),
    description: str = typer.Option("", "--description", "-d"),
    version: str = typer.Option("0.1.0", "--version"),
    examples_file: Optional[list[Path]] = typer.Option(
        None, "--examples-file", "-e",
        help="examples JSON/JSONL 文件 (可多次)",
    ),
    personality: Optional[list[str]] = typer.Option(
        None, "--personality", "-p",
        help="personality_traits 词 (可多次)",
    ),
    recommended_model: Optional[list[str]] = typer.Option(
        None, "--recommended-model", "-m",
        help="recommended_models (可多次)",
    ),
    expiry_hours: int = typer.Option(
        DEFAULT_SKILL_EXPIRY_HOURS, "--expiry-hours",
        help="IPFS pin 过期 hours [1, 168]",
    ),
    owner_did: Optional[str] = typer.Option(None, "--owner-did"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """训新 skill 并打包保存. owner 端 only."""
    if not system_prompt and not from_file:
        typer.echo("错: --system-prompt 或 --from-file 至少给一个", err=True)
        raise typer.Exit(code=2)
    if from_file:
        if not from_file.exists():
            typer.echo(f"错: from_file 不存在 {from_file}", err=True)
            raise typer.Exit(code=2)
        sp = from_file.read_text(encoding="utf-8")
    else:
        sp = system_prompt or ""

    own_did = _resolve_own_did(owner_did)

    try:
        pkg = package_skill(
            name=name,
            owner_did=own_did,
            system_prompt=sp,
            description=description,
            version=version,
            examples_files=list(examples_file or []),
            personality_traits=list(personality or []),
            recommended_models=list(recommended_model or []),
            expiry_hours=expiry_hours,
        )
    except (InvalidSkillPackageError, FileNotFoundError) as e:
        typer.echo(f"打包失败: {e}", err=True)
        raise typer.Exit(code=1)

    path = _save_owned_skill(pkg)
    if json_out:
        typer.echo(json.dumps(
            {
                "skill_id": pkg.skill_id,
                "qualified_name": pkg.qualified_name,
                "fingerprint": pkg.fingerprint,
                "saved_to": str(path),
                "version": pkg.version,
                "examples_count": pkg.contents.few_shot_examples_count,
                "personality_traits": pkg.contents.personality_traits,
            },
            ensure_ascii=False, indent=2,
        ))
    else:
        typer.echo(f"已创建 skill: {pkg.qualified_name}")
        typer.echo(f"  version          : {pkg.version}")
        typer.echo(f"  fingerprint      : {pkg.fingerprint}")
        typer.echo(f"  examples         : {pkg.contents.few_shot_examples_count}")
        typer.echo(f"  personality      : {pkg.contents.personality_traits}")
        typer.echo(f"  recommended_models: {pkg.contents.recommended_models}")
        typer.echo(f"  saved_to         : {path}")


@skill_app.command("list")
def cmd_list(
    owned: bool = typer.Option(True, "--owned/--no-owned"),
    available_to_borrow: bool = typer.Option(
        False, "--available-to-borrow",
        help="列朋友 lend 出来的 skill (从本地 SkillPinDB owner_did != self)",
    ),
    owner_did: Optional[str] = typer.Option(None, "--owner-did"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """列本机 owned skills + 可借 skill."""
    own_did = _resolve_own_did(owner_did)
    out: dict[str, list[dict]] = {"owned": [], "available_to_borrow": []}

    if owned:
        for sid in _list_owned_skill_ids():
            try:
                pkg = _load_owned_skill(sid)
                out["owned"].append({
                    "skill_id": pkg.skill_id,
                    "qualified_name": pkg.qualified_name,
                    "version": pkg.version,
                    "description": pkg.description,
                    "fingerprint": pkg.fingerprint,
                })
            except Exception as e:  # noqa: BLE001
                out["owned"].append({"skill_id": sid, "error": str(e)})

    if available_to_borrow:
        try:
            with SkillPinDB() as db:
                pins = db.list_active(limit=200)
            for p in pins:
                if p.owner_did != own_did:
                    out["available_to_borrow"].append({
                        "cid": p.cid,
                        "owner_did": p.owner_did,
                        "skill_id": p.skill_id,
                        "expires_at": p.expires_at,
                        "size_bytes": p.size_bytes,
                    })
        except Exception as e:  # noqa: BLE001
            typer.echo(f"warn: 无法读 SkillPinDB ({type(e).__name__}: {e})", err=True)

    if json_out:
        typer.echo(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        typer.echo(f"owned skills ({len(out['owned'])}):")
        for s in out["owned"]:
            line = f"  - {s.get('qualified_name') or s.get('skill_id')}"
            if "version" in s:
                line += f"  v{s['version']}  fp={s.get('fingerprint','')}"
            typer.echo(line)
        if available_to_borrow:
            typer.echo(f"\navailable to borrow ({len(out['available_to_borrow'])}):")
            for s in out["available_to_borrow"]:
                typer.echo(
                    f"  - {s['owner_did']}:{s['skill_id']}  cid={s['cid']}  "
                    f"expires_at={s['expires_at']}"
                )


@skill_app.command("lend")
def cmd_lend(
    skill_id: str = typer.Argument(..., help="本机 owned skill_id"),
    max_duration: int = typer.Option(
        DEFAULT_BORROW_DURATION_MINUTES, "--max-duration",
        help="borrower 单次可借最大分钟数 (写本地 skill 文件元 hint)",
    ),
    pin_to_ipfs: bool = typer.Option(
        False, "--pin/--no-pin",
        help="是否立即加密 + 上 IPFS (默认 no, lend 仅标可借, borrow 时再 pin)",
    ),
    recipient_pubkey_b64: Optional[str] = typer.Option(
        None, "--recipient-pubkey-b64",
        help="--pin 时必填; base64 编码的 borrower Curve25519 pubkey (32B)",
    ),
    expiry_hours: int = typer.Option(
        DEFAULT_SKILL_EXPIRY_HOURS, "--expiry-hours",
    ),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """把 skill 设为可借 (CLI 占位, 真生产由 PWA / daemon 主动接 P2P borrow request)."""
    pkg = _load_owned_skill(skill_id)
    out: dict = {
        "skill_id": pkg.skill_id,
        "qualified_name": pkg.qualified_name,
        "max_duration_minutes": max_duration,
        "pin_to_ipfs": pin_to_ipfs,
    }

    if pin_to_ipfs:
        if not recipient_pubkey_b64:
            typer.echo("错: --pin 时必须给 --recipient-pubkey-b64", err=True)
            raise typer.Exit(code=2)
        try:
            from nacl.public import PrivateKey
            from sisoul.friend.encrypted_proxy import derive_friend_session_keypair
            from sisoul.identity.seed import load_mnemonic_from_file, mnemonic_to_master_key
        except Exception as e:
            typer.echo(f"加密依赖不可用: {e}", err=True)
            raise typer.Exit(code=1)

        try:
            mnemonic = load_mnemonic_from_file()
            master = mnemonic_to_master_key(mnemonic)
            sender_priv, _ = derive_friend_session_keypair(master, friend_index=0)
        except Exception as e:
            typer.echo(f"派生 sender keypair 失败: {e}", err=True)
            raise typer.Exit(code=1)

        recipient_pub_bytes = base64.b64decode(recipient_pubkey_b64.encode("ascii"))
        try:
            blob = encrypt_skill_package(pkg, recipient_pub_bytes, sender_priv)
            from sisoul.friend.skill_ipfs import pin_skill_to_ipfs
            rec = pin_skill_to_ipfs(
                blob,
                owner_did=pkg.owner_did,
                skill_id=pkg.skill_id,
                expiry_hours=expiry_hours,
            )
            out["ipfs_cid"] = rec.cid
            out["pin_record"] = rec.to_dict()
        except (InvalidSkillPackageError, SkillIPFSError, ValueError) as e:
            typer.echo(f"lend pin 失败: {e}", err=True)
            raise typer.Exit(code=1)

    if json_out:
        typer.echo(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        typer.echo(f"skill {pkg.qualified_name} 可借 (max_duration={max_duration}min)")
        if "ipfs_cid" in out:
            typer.echo(f"  ipfs_cid: {out['ipfs_cid']}")


@skill_app.command("borrow")
def cmd_borrow(
    qualified_name: str = typer.Argument(..., help="<owner_did>:<skill_name>"),
    duration: int = typer.Option(
        DEFAULT_BORROW_DURATION_MINUTES, "--duration", "-d",
        help="borrow 分钟数 [1, 1440]",
    ),
    duration_seconds_override: Optional[int] = typer.Option(
        None, "--duration-test",
        help="(test) 用秒数 override duration, 30s 缩短 lifecycle 验证",
    ),
    borrower_did: Optional[str] = typer.Option(None, "--borrower-did"),
    skip_permission: bool = typer.Option(
        False, "--skip-permission",
        help="(test/dev) 跳过 friend / dev-C permission 检查",
    ),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """借朋友 skill, 起 30min borrow session.

    真生产: daemon 路由调 P2P 跟 owner 协商 + 加密 / IPFS pin / 派发 key.
    CLI: 走 daemon HTTP /sisoul/skill/borrow.
    本子命令 fallback: 如果 owner 是 self (相同 own_did), 直接走本机 owned skill 走 round-trip
        (开发期 self-loop 验证 lifecycle).
    """
    try:
        owner_did, skill_id = parse_qualified_name(qualified_name)
    except InvalidSkillPackageError as e:
        typer.echo(f"qualified_name 解析失败: {e}", err=True)
        raise typer.Exit(code=2)

    own_did = _resolve_own_did(borrower_did)

    # self-loop 模式: owner == self → 直接走本地 owned skill 加密 round-trip
    if owner_did == own_did:
        try:
            pkg = _load_owned_skill(skill_id)
        except Exception:
            typer.echo(
                f"self-loop borrow 失败: 本机 owned 没找到 {skill_id} (先 sisoul skill create)",
                err=True,
            )
            raise typer.Exit(code=1)

        from nacl.public import PrivateKey, PublicKey
        from sisoul.friend.encrypted_proxy import derive_friend_session_keypair
        from sisoul.identity.seed import load_mnemonic_from_file, mnemonic_to_master_key

        try:
            mnemonic = load_mnemonic_from_file()
            master = mnemonic_to_master_key(mnemonic)
            priv, pub = derive_friend_session_keypair(master, friend_index=0)
        except Exception as e:
            typer.echo(f"派生 self keypair 失败: {e}", err=True)
            raise typer.Exit(code=1)

        # round-trip self-encrypt
        def provider(_o: str, _s: str) -> tuple[bytes, str]:
            blob = encrypt_skill_package(pkg, pub, priv)
            from sisoul.friend.skill_ipfs import register_mock_blob
            import hashlib
            cid = "mockcid-" + hashlib.sha256(blob).hexdigest()[:46]
            register_mock_blob(cid, blob)
            return blob, cid

        def decryptor(blob: bytes) -> SkillPackage:
            return decrypt_skill_package(blob, pub, priv)

        try:
            res = request_borrow_skill(
                owner_did=owner_did,
                skill_id=skill_id,
                borrower_did=own_did,
                duration_minutes=duration,
                duration_seconds_override=duration_seconds_override,
                encrypted_skill_provider=provider,
                decrypt_callback=decryptor,
                skip_permission_check=True,  # self-loop 不需检 permission
            )
        except SkillBorrowError as e:
            typer.echo(f"borrow 失败: {e}", err=True)
            raise typer.Exit(code=1)

        if json_out:
            typer.echo(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
        else:
            typer.echo(f"borrow session 已起 (self-loop): {res.session.session_id}")
            typer.echo(f"  qualified_name : {res.session.qualified_name}")
            typer.echo(f"  expires_at     : {res.session.expires_at}")
            typer.echo(f"  duration_min   : {res.session.duration_minutes}")
            typer.echo(f"  local_decrypted: {res.session.local_decrypted_path}")
            typer.echo(f"  ipfs_cid       : {res.session.ipfs_cid}")
            typer.echo(f"  fingerprint    : {res.skill_package_fingerprint}")
            typer.echo(f"  permission     : {res.permission_reason}")
        return

    # 远程 borrow (走 daemon HTTP)
    typer.echo(
        "远程 borrow 走 daemon HTTP POST /sisoul/skill/borrow (Phase 5 P2P 真实现).\n"
        "当前 CLI 仅支持 self-loop 借自己的 skill 验证 lifecycle. "
        f"qualified_name={qualified_name} 的 owner != self ({own_did}).",
        err=True,
    )
    raise typer.Exit(code=1)


@skill_app.command("sessions")
def cmd_sessions(
    mine: bool = typer.Option(
        False, "--mine",
        help="列我作为 owner 借出的 sessions (owner_did=me)",
    ),
    mine_as_borrower: bool = typer.Option(
        True, "--mine-as-borrower/--no-mine-as-borrower",
        help="列我作为 borrower 借入的 sessions (默认 on)",
    ),
    show_all: bool = typer.Option(
        False, "--all",
        help="包含 destroyed / expired (默认只列 active)",
    ),
    own_did_arg: Optional[str] = typer.Option(None, "--own-did"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """列 active borrow sessions."""
    own_did = _resolve_own_did(own_did_arg)
    sessions: list = []
    if mine_as_borrower:
        sessions += list_borrow_sessions(borrower_did=own_did, only_active=not show_all)
    if mine:
        sessions += list_borrow_sessions(owner_did=own_did, only_active=not show_all)

    # 去重 by session_id
    seen = set()
    uniq = []
    for s in sessions:
        if s.session_id in seen:
            continue
        seen.add(s.session_id)
        uniq.append(s)

    if json_out:
        typer.echo(json.dumps(
            [s.to_dict() for s in uniq], ensure_ascii=False, indent=2,
        ))
    else:
        if not uniq:
            typer.echo("no skill borrow sessions")
            return
        for s in uniq:
            typer.echo(
                f"- {s.session_id}  status={s.status}  "
                f"borrower={s.borrower_did} owner={s.owner_did} "
                f"skill={s.skill_id} expires_at={s.expires_at}"
            )


@skill_app.command("end-session")
def cmd_end_session(
    session_id: str = typer.Argument(...),
    reason: str = typer.Option("manual", "--reason"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """主动结束 borrow session: wipe tmp + IPFS unpin + ledger."""
    try:
        s = end_skill_borrow_session(session_id, reason=reason)
    except SkillBorrowError as e:
        typer.echo(f"end 失败: {e}", err=True)
        raise typer.Exit(code=1)

    if json_out:
        typer.echo(json.dumps(s.to_dict(), ensure_ascii=False, indent=2))
    else:
        typer.echo(f"ended: {s.session_id}")
        typer.echo(f"  status        : {s.status}")
        typer.echo(f"  destroy_reason: {s.destroy_reason}")
        typer.echo(f"  destroyed_at  : {s.destroyed_at}")
        typer.echo(f"  ledger_entry  : {s.ledger_entry_id}")


__all__ = ["skill_app"]
