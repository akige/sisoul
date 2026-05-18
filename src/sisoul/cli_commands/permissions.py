"""sisoul perms 命令组 · 5 子命令 (Phase 4 W59-W65, 波 5 dev-C).

5 子命令 (Typer subapp `perms_app`):
- sisoul perms list [--friend DID]                           列朋友权限
- sisoul perms set <friend_did> --mode ... --resource ...    改授权
- sisoul perms revoke <friend_did>                           即时撤销 + 链上 REVOKE
- sisoul perms reputation [--friend DID]                     查自己/朋友 reputation
- sisoul perms scan-log                                       L5 scan 拦截日志

由 cli.py 主入口通过 ``app.add_typer(perms_app, name="perms")`` 整合 (主集成 layer 负责).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from sisoul.friend.anti_abuse import (
    compute_reputation,
    list_scan_log,
    publish_reputation_attestation,
    revoke_friend_permission,
)
from sisoul.friend.permissions import (
    AISkillShare,
    ComputeShare,
    FriendPermission,
    InvalidPermissionConfigError,
    LLMQuotaShare,
    PermissionNotFoundError,
    VALID_MODES,
    VALID_RESOURCES,
    list_all_friends,
    load_permissions,
    save_permissions,
    unmark_revoked,
)

perms_app = typer.Typer(
    name="perms",
    help="朋友授权 (3 档模式) + 滥用防御 (5 层). Phase 4 W59-W65.",
    no_args_is_help=True,
)


def _perms_dir(perms_dir: Optional[Path]) -> Optional[Path]:
    return perms_dir if perms_dir else None


# ── perms list ───────────────────────────────────────────────────────────────


@perms_app.command("list")
def cmd_list(
    friend: Optional[str] = typer.Option(
        None, "--friend", "-f", help="只列某朋友 DID (省略=全部)"
    ),
    perms_dir: Optional[Path] = typer.Option(
        None, "--perms-dir", help="自定义 perms 目录 (默认 ~/.sisoul/friends/)"
    ),
    json_output: bool = typer.Option(False, "--json", help="JSON 输出"),
) -> None:
    """列朋友权限."""
    pd = _perms_dir(perms_dir)
    if friend:
        try:
            perm = load_permissions(friend, pd)
        except PermissionNotFoundError as e:
            typer.echo(f"(无 perm) {e}", err=True)
            raise typer.Exit(code=1)
        if json_output:
            typer.echo(json.dumps(perm.to_dict(), ensure_ascii=False, indent=2))
        else:
            _print_perm(perm)
        return

    friends = list_all_friends(pd)
    if not friends:
        typer.echo("(无朋友 perm 记录)")
        return
    if json_output:
        out = []
        for f in friends:
            try:
                p = load_permissions(f, pd)
                out.append(p.to_dict())
            except Exception as e:
                out.append({"friend": f, "error": str(e)})
        typer.echo(json.dumps(out, ensure_ascii=False, indent=2))
        return

    typer.echo(f"共 {len(friends)} 个朋友:")
    typer.echo("| friend_did | revoked | llm.mode | llm.cap | llm.rate | skill.mode |")
    typer.echo("|---|---|---|---|---|---|")
    for f in friends:
        try:
            p = load_permissions(f, pd)
            typer.echo(
                f"| {f} | {p.revoked} | {p.llm_quota_share.mode} | "
                f"{p.llm_quota_share.monthly_token_cap} | "
                f"{p.llm_quota_share.rate_limit} | {p.ai_skill_share.mode} |"
            )
        except Exception as e:
            typer.echo(f"| {f} | ERR | {e} | - | - | - |")


def _print_perm(p: FriendPermission) -> None:
    typer.echo(f"friend: {p.friend_did}")
    typer.echo(f"  revoked: {p.revoked} (at={p.revoked_at}, reason={p.revoked_reason})")
    typer.echo(
        f"  llm_quota_share: enabled={p.llm_quota_share.enabled} "
        f"mode={p.llm_quota_share.mode} cap={p.llm_quota_share.monthly_token_cap} "
        f"rate={p.llm_quota_share.rate_limit}/min "
        f"models={p.llm_quota_share.models} "
        f"reserve={p.llm_quota_share.emergency_reserve_tokens}"
    )
    typer.echo(
        f"  ai_skill_share: enabled={p.ai_skill_share.enabled} "
        f"mode={p.ai_skill_share.mode} skills={p.ai_skill_share.skills} "
        f"per_session_max={p.ai_skill_share.per_session_max_minutes}min"
    )
    typer.echo(
        f"  compute_share: enabled={p.compute_share.enabled} "
        f"mode={p.compute_share.mode} (v2/v3)"
    )


# ── perms set ────────────────────────────────────────────────────────────────


@perms_app.command("set")
def cmd_set(
    friend_did: str = typer.Argument(..., help="朋友 DID"),
    mode: str = typer.Option(
        "per-request",
        "--mode",
        help=f"授权模式: {' | '.join(VALID_MODES)}",
    ),
    resource: str = typer.Option(
        "llm_quota",
        "--resource",
        help=f"资源: {' | '.join(VALID_RESOURCES)}",
    ),
    monthly_cap: int = typer.Option(
        0, "--monthly-cap", help="monthly_token_cap (llm_quota 用; 0 = 不限)"
    ),
    rate_limit: int = typer.Option(
        0, "--rate-limit", help="rate_limit N/min (llm_quota 用; 0 = 不限)"
    ),
    emergency_reserve: int = typer.Option(
        0,
        "--emergency-reserve",
        help="emergency_reserve_tokens (emergency-only 模式 reserve quota)",
    ),
    model: list[str] = typer.Option(
        None,
        "--model",
        "-m",
        help="model allow list, 重复使用; 不传 = 全部允许 (--clear-models 显式清空)",
    ),
    skill: list[str] = typer.Option(
        None, "--skill", "-s", help="ai_skill allow list, 重复使用"
    ),
    per_session_max_minutes: int = typer.Option(
        0,
        "--per-session-max-minutes",
        help="单次 borrowed session 最大分钟数 (ai_skill 用)",
    ),
    enabled: bool = typer.Option(True, "--enabled/--disabled"),
    clear_models: bool = typer.Option(
        False, "--clear-models", help="显式清空 model 列表 (变为 '全部允许')"
    ),
    perms_dir: Optional[Path] = typer.Option(None, "--perms-dir"),
) -> None:
    """配/改朋友某 resource 的授权 (合并式 patch, 不影响其他 resource)."""
    if mode not in VALID_MODES:
        typer.echo(f"mode 非法: {mode}; 必须 ∈ {VALID_MODES}", err=True)
        raise typer.Exit(code=2)
    if resource not in VALID_RESOURCES:
        typer.echo(f"resource 非法: {resource}; 必须 ∈ {VALID_RESOURCES}", err=True)
        raise typer.Exit(code=2)

    pd = _perms_dir(perms_dir)
    try:
        perm = load_permissions(friend_did, pd)
    except PermissionNotFoundError:
        perm = FriendPermission(friend_did=friend_did)

    if resource == "llm_quota":
        sub = perm.llm_quota_share
        sub.enabled = enabled
        sub.mode = mode
        sub.monthly_token_cap = int(monthly_cap)
        sub.rate_limit = int(rate_limit)
        sub.emergency_reserve_tokens = int(emergency_reserve)
        if clear_models:
            sub.models = []
        elif model:
            sub.models = list(model)
    elif resource == "ai_skill":
        sub2 = perm.ai_skill_share
        sub2.enabled = enabled
        sub2.mode = mode
        if skill is not None:
            sub2.skills = list(skill)
        if per_session_max_minutes:
            sub2.per_session_max_minutes = int(per_session_max_minutes)
    else:  # compute
        sub3 = perm.compute_share
        sub3.enabled = enabled
        sub3.mode = mode

    try:
        path = save_permissions(friend_did, perm, pd)
    except InvalidPermissionConfigError as e:
        typer.echo(f"perm 校验失败: {e}", err=True)
        raise typer.Exit(code=2)

    typer.echo(f"saved: {path}")
    _print_perm(perm)


# ── perms revoke ─────────────────────────────────────────────────────────────


@perms_app.command("revoke")
def cmd_revoke(
    friend_did: str = typer.Argument(..., help="朋友 DID"),
    reason: str = typer.Option("", "--reason", "-r", help="revoke 原因 (写链上)"),
    perms_dir: Optional[Path] = typer.Option(None, "--perms-dir"),
    undo: bool = typer.Option(False, "--undo", help="撤销 revoke (改主意)"),
) -> None:
    """L3 即时撤销 + 链上 REVOKE attestation (或 --undo 取消 revoke)."""
    pd = _perms_dir(perms_dir)
    if undo:
        try:
            p = unmark_revoked(friend_did, pd)
        except PermissionNotFoundError as e:
            typer.echo(f"perm 不存在, 无可 undo: {e}", err=True)
            raise typer.Exit(code=1)
        typer.echo(f"undone revoke: {friend_did}")
        _print_perm(p)
        return

    result = revoke_friend_permission(friend_did, reason=reason, perms_dir=pd)
    typer.echo(
        f"revoked: {friend_did} at={result['revoked_at']} reason={reason!r}"
    )
    typer.echo(f"  EAS attestation queue_id: {result['attestation_queue_id']}")


# ── perms reputation ─────────────────────────────────────────────────────────


@perms_app.command("reputation")
def cmd_reputation(
    friend: Optional[str] = typer.Option(
        None, "--friend", "-f", help="朋友 DID (省略=自己; 自己需 --self-did)"
    ),
    self_did: Optional[str] = typer.Option(
        None, "--self-did", help="自己 DID (省略 friend 时用)"
    ),
    borrows: int = typer.Option(0, "--borrows", help="过去 30d 借入次数 (手填或 dev-D ledger 注入)"),
    lends: int = typer.Option(0, "--lends", help="过去 30d 借出次数"),
    abuse: int = typer.Option(0, "--abuse-incidents", help="历史滥用次数"),
    spam: int = typer.Option(0, "--spam-complaints", help="历史 spam 投诉"),
    publish: bool = typer.Option(False, "--publish", help="同时上链 REPUTATION_PUBLISH"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """查自己 / 朋友 reputation (L4). dev-D ledger 接入后, borrows/lends/incidents 可自动拉."""
    did = friend or self_did
    if not did:
        typer.echo(
            "需指定 --friend <DID> 或 --self-did <DID> (Phase 5 接 identity registry 自动拉自己)",
            err=True,
        )
        raise typer.Exit(code=2)

    rep = compute_reputation(
        did,
        borrows=borrows,
        lends=lends,
        abuse_incidents=abuse,
        spam_complaints=spam,
    )

    if json_output:
        out = rep.to_dict()
        if publish:
            qid = publish_reputation_attestation(rep)
            out["attestation_queue_id"] = qid
        typer.echo(json.dumps(out, ensure_ascii=False, indent=2))
        return

    typer.echo(f"reputation for {did}:")
    typer.echo(f"  score: {rep.score} (grade {rep.grade})")
    typer.echo(f"  borrows: {rep.borrows}  lends: {rep.lends}  ratio: {rep.balance_ratio}")
    typer.echo(f"  abuse_incidents: {rep.abuse_incidents}  spam_complaints: {rep.spam_complaints}")
    typer.echo(f"  computed_at: {rep.computed_at}")
    if publish:
        qid = publish_reputation_attestation(rep)
        typer.echo(f"  EAS attestation queue_id: {qid}")


# ── perms scan-log ───────────────────────────────────────────────────────────


@perms_app.command("scan-log")
def cmd_scan_log(
    friend: Optional[str] = typer.Option(
        None, "--friend", "-f", help="只看某朋友"
    ),
    limit: int = typer.Option(20, "--limit", "-n"),
    only_blocked: bool = typer.Option(
        True, "--only-blocked/--all", help="默认只列被 block 的; --all 列全部"
    ),
    scan_db: Optional[Path] = typer.Option(None, "--scan-db"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """查 L5 scan 拦截日志 (最近 N 条)."""
    rows = list_scan_log(
        limit=limit,
        friend_did=friend,
        only_blocked=only_blocked,
        db_path=scan_db,
    )
    if json_output:
        typer.echo(json.dumps(rows, ensure_ascii=False, indent=2))
        return

    if not rows:
        typer.echo("(无记录)")
        return
    typer.echo(f"最近 {len(rows)} 条 scan event:")
    typer.echo("| id | ts | friend | allowed | amount | reason |")
    typer.echo("|---|---|---|---|---|---|")
    for r in rows:
        typer.echo(
            f"| {r['id']} | {r['ts']} | {r['friend_did']} | "
            f"{r['allowed']} | {r['amount']} | {r['reason']} |"
        )


__all__ = ["perms_app"]
