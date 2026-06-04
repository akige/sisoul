"""sisoul friend 命令组 (Phase 4 W51-W53 · 波 5 dev-A).

5 子命令 (Typer subapp ``friend_app``):
- request <did> [--message MSG]    发 FRIEND_REQUEST
- accept <request_id>              accept 入站 request → 双向 attestation
- list [--show-score] [--status S] 列本地 friends
- revoke <did>                     revoke FRIEND
- info <did>                       查 friend 详情 + 强连接评分细分 (+ 互惠 ledger 摘要 stub)

主集成 cli.py 由父会话整合 (波 5 主集成阶段):
    from sisoul.cli_commands.friend import friend_app
    app.add_typer(friend_app, name='friend')

cli.py 当前的 stub ``friend(action, target)`` 函数也由父集成移除.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from sisoul.friend.relationship import (
    FriendError,
    FriendNotFoundError,
    FriendRelationship,
    FriendRequestError,
    FriendRequestNotFoundError,
    compute_strong_tie_score,
    resolve_own_did,
)
from sisoul.identity.did_key import (
    DidKeyError,
    decode_did_key,
)

friend_app = typer.Typer(
    name="friend",
    help=(
        "朋友关系层 (DID + 双向 EAS attestation + 强连接评分). "
        "Phase 4 W51-W53 · 波 5 dev-A."
    ),
    no_args_is_help=True,
)


def _resolve_own_did(own_did_opt: Optional[str], vault_dir: Optional[Path]) -> str:
    """命令公共: own_did 解析 (显式 --own-did 优先, 否则 DID registry 第一条)."""
    if own_did_opt:
        return own_did_opt
    registry_path = (
        (vault_dir / "identity" / "dids.json") if vault_dir else None
    )
    return resolve_own_did(registry_path=registry_path)


def _rel(
    own_did_opt: Optional[str],
    vault_dir: Optional[Path],
    friend_db: Optional[Path],
    attest_queue_db: Optional[Path],
) -> FriendRelationship:
    own_did = _resolve_own_did(own_did_opt, vault_dir)
    return FriendRelationship(
        own_did=own_did,
        db_path=friend_db,
        attest_queue_db=attest_queue_db,
    )


# ── friend request ──────────────────────────────────────────────────────────


@friend_app.command("request")
def cmd_request(
    did: str = typer.Argument(..., help="目标朋友 DID (did:sisoul:bob 或 bob.sisoul.eth)"),
    message: str = typer.Option("", "--message", "-m", help="可选个人留言"),
    own_did: Optional[str] = typer.Option(
        None, "--own-did", help="覆盖本端 DID (默认本机 registry 第一条)"
    ),
    vault_dir: Optional[Path] = typer.Option(
        None, "--vault-dir", help="vault 目录 (找 identity/dids.json)"
    ),
    friend_db: Optional[Path] = typer.Option(
        None, "--friend-db", help="SQLite cache 路径 (默认 ~/.sisoul/friends.db)"
    ),
    attest_queue_db: Optional[Path] = typer.Option(
        None, "--attest-queue-db", help="EAS attest queue 路径 (默认 ~/.sisoul/attest_queue.db)"
    ),
    json_output: bool = typer.Option(False, "--json", help="JSON 输出"),
) -> None:
    """发 FRIEND_REQUEST attestation 给对方."""
    try:
        rel = _rel(own_did, vault_dir, friend_db, attest_queue_db)
        req = rel.send_friend_request(did, message=message)
    except (FriendError, FriendRequestError) as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(code=1)

    if json_output:
        typer.echo(json.dumps(req.to_dict(), ensure_ascii=False, indent=2))
        return
    typer.echo(f"✅ friend request 已发出 → {req.target_did}")
    typer.echo(f"  request_id:      {req.request_id}")
    typer.echo(f"  attestation_uid: {req.attestation_uid}")
    typer.echo(f"  status:          {req.status} (待对方 accept)")
    if message:
        typer.echo(f"  message:         {message}")


# ── friend accept ──────────────────────────────────────────────────────────


@friend_app.command("accept")
def cmd_accept(
    request_id: str = typer.Argument(..., help="inbound request_id (来自 friend list-requests)"),
    own_did: Optional[str] = typer.Option(None, "--own-did"),
    vault_dir: Optional[Path] = typer.Option(None, "--vault-dir"),
    friend_db: Optional[Path] = typer.Option(None, "--friend-db"),
    attest_queue_db: Optional[Path] = typer.Option(None, "--attest-queue-db"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """accept inbound FRIEND_REQUEST → 上链 FRIEND_ACCEPT (双向 attestation 完成)."""
    try:
        rel = _rel(own_did, vault_dir, friend_db, attest_queue_db)
        friend = rel.accept_friend_request(request_id)
    except (FriendError, FriendRequestError, FriendRequestNotFoundError) as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(code=1)

    if json_output:
        typer.echo(json.dumps(friend.to_dict(), ensure_ascii=False, indent=2))
        return
    typer.echo(f"✅ accepted friend → {friend.did}")
    typer.echo(f"  status:                  {friend.status}")
    typer.echo(f"  accept_attestation_uid:  {friend.accept_attestation_uid}")
    typer.echo(f"  became_active_at:        {friend.became_active_at}")
    if not friend.is_mutual:
        typer.echo(
            "  (mutual=False · 等对方 sisoul daemon 收到 FRIEND_ACCEPT 后 confirm)"
        )


# ── friend list ──────────────────────────────────────────────────────────


@friend_app.command("list")
def cmd_list(
    show_score: bool = typer.Option(
        False, "--show-score", "-s", help="重算并显示强连接评分细分"
    ),
    status: Optional[str] = typer.Option(
        None, "--status",
        help="过滤 status: pending / active / revoked (默认全列)"
    ),
    own_did: Optional[str] = typer.Option(None, "--own-did"),
    vault_dir: Optional[Path] = typer.Option(None, "--vault-dir"),
    friend_db: Optional[Path] = typer.Option(None, "--friend-db"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """列本地 friends cache."""
    try:
        rel = _rel(own_did, vault_dir, friend_db, None)
        # status 校验
        if status and status not in {"pending", "active", "revoked"}:
            typer.echo(
                f"ERROR: --status 必须是 pending/active/revoked (拿到 '{status}')",
                err=True,
            )
            raise typer.Exit(code=1)
        items = rel.list_friends(status=status, recompute_score=show_score)  # type: ignore[arg-type]
    except FriendError as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(code=1)

    if json_output:
        out = []
        for fr in items:
            d = fr.to_dict()
            if show_score:
                d["score_breakdown"] = compute_strong_tie_score(fr).to_dict()
            out.append(d)
        typer.echo(json.dumps(out, ensure_ascii=False, indent=2))
        return

    if not items:
        typer.echo("(本地无 friend)")
        return

    # P2-CD: load petname store (无文件就空)
    from sisoul.friend.petname import PetnameStore
    pn_store = PetnameStore().load()

    def _name(did: str) -> str:
        return pn_store.display_name(did)

    if show_score:
        typer.echo(
            "| name | did | status | mutual | score | strong? | months | interactions |"
        )
        typer.echo("|---|---|---|---|---|---|---|---|")
        for fr in items:
            sc = compute_strong_tie_score(fr)
            typer.echo(
                f"| {_name(fr.did)} | {fr.did} | {fr.status} | {fr.is_mutual} | "
                f"{sc.total:.2f} | {sc.is_strong} | "
                f"{sc.months_elapsed:.1f} | {sc.interaction_count} |"
            )
    else:
        typer.echo("| name | did | status | mutual | created_at |")
        typer.echo("|---|---|---|---|---|")
        for fr in items:
            typer.echo(
                f"| {_name(fr.did)} | {fr.did} | {fr.status} | "
                f"{fr.is_mutual} | {fr.created_at} |"
            )


# ── friend revoke ──────────────────────────────────────────────────────────


@friend_app.command("revoke")
def cmd_revoke(
    did: str = typer.Argument(..., help="目标 friend DID"),
    own_did: Optional[str] = typer.Option(None, "--own-did"),
    vault_dir: Optional[Path] = typer.Option(None, "--vault-dir"),
    friend_db: Optional[Path] = typer.Option(None, "--friend-db"),
    attest_queue_db: Optional[Path] = typer.Option(None, "--attest-queue-db"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """revoke FRIEND → 上链 REVOKE attestation + 本端标 revoked."""
    try:
        rel = _rel(own_did, vault_dir, friend_db, attest_queue_db)
        friend = rel.revoke_friend(did)
    except (FriendError, FriendNotFoundError) as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(code=1)

    if json_output:
        typer.echo(json.dumps(friend.to_dict(), ensure_ascii=False, indent=2))
        return
    typer.echo(f"✅ revoked friend → {friend.did}")
    typer.echo(f"  status:                  {friend.status}")
    typer.echo(f"  revoke_attestation_uid:  {friend.revoke_attestation_uid}")


# ── friend info ──────────────────────────────────────────────────────────


@friend_app.command("info")
def cmd_info(
    did: str = typer.Argument(..., help="目标 friend DID"),
    own_did: Optional[str] = typer.Option(None, "--own-did"),
    vault_dir: Optional[Path] = typer.Option(None, "--vault-dir"),
    friend_db: Optional[Path] = typer.Option(None, "--friend-db"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """查 friend 详情 + 强连接评分细分 + 互惠 ledger 摘要 (后者 dev-D ship 后才填)."""
    try:
        rel = _rel(own_did, vault_dir, friend_db, None)
        friend = rel.get_friend(did)
        score = compute_strong_tie_score(friend)
    except (FriendError, FriendNotFoundError) as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(code=1)

    ledger_summary: dict[str, object] = {"available": False, "reason": "dev-D 波 5 未 ship"}
    try:
        # dev-D ship 后这里就能 import; 没 ship 时 ImportError fail-open.
        from sisoul.friend.ledger import summarize_friend_ledger  # type: ignore[attr-defined]

        ledger_summary = summarize_friend_ledger(  # type: ignore[no-any-return]
            own_did=rel.own_did, friend_did=friend.did
        )
    except Exception:  # noqa: BLE001
        pass

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "friend": friend.to_dict(),
                    "score_breakdown": score.to_dict(),
                    "ledger_summary": ledger_summary,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    from sisoul.friend.petname import PetnameStore
    _petname = PetnameStore().load().get(friend.did) or "-"

    typer.echo(f"friend: {friend.did}")
    typer.echo(f"  petname:                 {_petname}")
    typer.echo(f"  handle:                  {friend.handle}")
    typer.echo(f"  status:                  {friend.status}")
    typer.echo(f"  mutual:                  {friend.is_mutual}")
    typer.echo(f"  created_at:              {friend.created_at}")
    typer.echo(f"  became_active_at:        {friend.became_active_at}")
    typer.echo(f"  last_interaction:        {friend.last_interaction}")
    typer.echo(f"  interaction_count:       {friend.interaction_count}")
    typer.echo(f"  request_attestation_uid: {friend.request_attestation_uid}")
    typer.echo(f"  accept_attestation_uid:  {friend.accept_attestation_uid}")
    typer.echo(f"  mutual_attestation_uid:  {friend.mutual_attestation_uid}")
    typer.echo(f"  revoke_attestation_uid:  {friend.revoke_attestation_uid}")
    typer.echo("--- 强连接评分细分 ---")
    typer.echo(f"  total:               {score.total:.2f}")
    typer.echo(f"  is_strong:           {score.is_strong} (阈值 5.0)")
    typer.echo(f"  base:                {score.base}")
    typer.echo(f"  months_score:        {score.months_score} (经过 {score.months_elapsed:.2f} 月)")
    typer.echo(f"  interactions_score:  {score.interactions_score} ({score.interaction_count} 次)")
    if score.manual_override is not None:
        typer.echo(f"  manual_override:     {score.manual_override}")
    typer.echo("--- 互惠 ledger 摘要 ---")
    typer.echo(f"  {ledger_summary}")


# ── friend add (Wave B' P0-3 · 加 did:key friend, 轻量, 无 EAS attestation) ──


def _did_key_friends_path(vault_dir: Optional[Path]) -> Path:
    if vault_dir:
        return Path(vault_dir) / "identity" / "didkey_friends.json"
    return Path.home() / ".sisoul" / "identity" / "didkey_friends.json"


def _load_did_key_friends(vault_dir: Optional[Path]) -> list[dict]:
    fp = _did_key_friends_path(vault_dir)
    if not fp.exists():
        return []
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def _save_did_key_friends(entries: list[dict], vault_dir: Optional[Path]) -> Path:
    fp = _did_key_friends_path(vault_dir)
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    return fp


@friend_app.command("add")
def cmd_add(
    friend_did: str = typer.Argument(
        ...,
        help=(
            "朋友 did:key:z... (W3C-CCG did:key, X25519 pubkey). "
            "对 did:sisoul:* 用 'sisoul friend request'."
        ),
    ),
    nickname: str = typer.Option(
        "", "--nickname", "-n", help="本地昵称 (可选, 仅本地显示)"
    ),
    vault_dir: Optional[Path] = typer.Option(
        None, "--vault-dir", help="vault 目录 (默认 ~/.sisoul/)"
    ),
    json_output: bool = typer.Option(False, "--json", help="JSON 输出"),
) -> None:
    """加 did:key 朋友 (轻量本地, 不发 EAS attestation, 不上链).

    Wave B' P0-3: did:key 朋友走 X25519 libsodium box 直接加密通信,
    无需双向 attestation. 跟 `friend request` (EAS attestation 重路径) 共存.
    """
    try:
        dk = decode_did_key(friend_did)
    except DidKeyError as e:
        typer.echo(f"ERROR: did:key 格式错误: {e}", err=True)
        raise typer.Exit(code=1)

    entries = _load_did_key_friends(vault_dir)
    existing_idx = None
    for i, e in enumerate(entries):
        if e.get("did") == dk.did:
            existing_idx = i
            break

    from datetime import datetime, timezone

    record = {
        "did": dk.did,
        "pubkey_hex": dk.pubkey.hex(),
        "key_type": dk.key_type,
        "nickname": nickname,
        "added_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "method": "did:key",
    }

    if existing_idx is not None:
        entries[existing_idx] = {**entries[existing_idx], **record}
        msg = "updated"
    else:
        entries.append(record)
        msg = "added"
    fp = _save_did_key_friends(entries, vault_dir)

    if json_output:
        typer.echo(
            json.dumps(
                {"action": msg, "record": record, "saved_to": str(fp)},
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    typer.echo(f"OK did:key friend {msg}: {dk.did}")
    typer.echo(f"  pubkey (hex): {dk.pubkey.hex()[:32]}... ({dk.key_type})")
    if nickname:
        typer.echo(f"  nickname: {nickname}")
    typer.echo(f"  saved: {fp}")
    typer.echo(
        "  note: did:key friend 走 libsodium box 直接加密, 无 EAS attestation. "
        "如需 EAS 双向 attestation 用 'sisoul friend request'."
    )


# ── friend mdns (P2-CD · 局域网朋友发现) ──────────────────────────────────────

mdns_app = typer.Typer(
    name="mdns",
    help="局域网朋友发现 (mDNS / Bonjour).",
    no_args_is_help=True,
)


@mdns_app.command("scan")
def cmd_mdns_scan(
    timeout: float = typer.Option(5.0, "--timeout", "-t", help="扫描秒数"),
    own_did_key: Optional[str] = typer.Option(
        None, "--own-did-key", help="过滤自己 (扫到 = 自己, 跳过)"
    ),
    json_output: bool = typer.Option(False, "--json", help="JSON 输出"),
) -> None:
    """扫局域网 sisoul peer (一次性 timeout 秒)."""
    from sisoul.friend.mdns import ZEROCONF_AVAILABLE, scan

    if not ZEROCONF_AVAILABLE:
        typer.echo("ERROR: zeroconf 未装, 'pip install zeroconf'", err=True)
        raise typer.Exit(code=1)
    peers = scan(timeout=timeout, own_did_key=own_did_key)
    if json_output:
        typer.echo(json.dumps(peers, ensure_ascii=False, indent=2))
        return
    if not peers:
        typer.echo(f"(局域网未发现 sisoul peer, 扫描 {timeout}s)")
        return
    typer.echo("| did_key | multiaddr | petname_hint | hostname |")
    typer.echo("|---|---|---|---|")
    for p in peers:
        typer.echo(
            f"| {p['did_key']} | {p['multiaddr']} | "
            f"{p['petname_hint']} | {p['hostname']} |"
        )


@mdns_app.command("announce")
def cmd_mdns_announce(
    did_key: str = typer.Argument(..., help="本端 did:key:z..."),
    multiaddr: Optional[str] = typer.Option(
        None, "--multiaddr", help="覆盖广播 multiaddr (默认本机 IP:port)"
    ),
    port: int = typer.Option(4001, "--port", "-p", help="广播端口"),
    petname_hint: str = typer.Option(
        "", "--petname-hint", help="本端展示 hint (对方扫到后可作昵称默认值)"
    ),
) -> None:
    """后台 announce 本端 sisoul service, SIGINT 退出."""
    from sisoul.friend.mdns import MDNSAnnouncer, ZEROCONF_AVAILABLE

    if not ZEROCONF_AVAILABLE:
        typer.echo("ERROR: zeroconf 未装", err=True)
        raise typer.Exit(code=1)
    ann = MDNSAnnouncer(
        did_key=did_key,
        multiaddr=multiaddr,
        port=port,
        petname_hint=petname_hint,
    )
    ann.start()
    typer.echo(
        f"OK mDNS announcing: {did_key} @ {ann.ip}:{port} "
        f"(petname_hint={petname_hint or '-'}). Ctrl-C 退出."
    )
    try:
        import time as _time
        while True:
            _time.sleep(3600)
    except KeyboardInterrupt:
        typer.echo("\n(SIGINT received, unregister)")
    finally:
        ann.stop()


friend_app.add_typer(mdns_app, name="mdns")


# ── friend petname (P2-CD · 本地昵称) ───────────────────────────────────────

petname_app = typer.Typer(
    name="petname",
    help="本地昵称 (did → petname 本地映射, 不上链).",
    no_args_is_help=True,
)


def _petname_store(path_opt: Optional[Path]):
    from sisoul.friend.petname import PetnameStore
    return PetnameStore(path=path_opt).load()


@petname_app.command("set")
def cmd_petname_set(
    did_key: str = typer.Argument(..., help="did:* (did:key:z... 或 did:sisoul:*)"),
    petname: str = typer.Argument(..., help="本地昵称"),
    store: Optional[Path] = typer.Option(
        None, "--store", help="petnames.json 路径 (默认 ~/.sisoul/petnames.json)"
    ),
    json_output: bool = typer.Option(False, "--json", help="JSON 输出"),
) -> None:
    """设 / 改本地 petname."""
    from sisoul.friend.petname import PetnameError
    try:
        st = _petname_store(store)
        st.set(did_key, petname)
    except PetnameError as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(code=1)
    if json_output:
        typer.echo(json.dumps({"did": did_key, "petname": petname, "saved_to": str(st.path)}))
        return
    typer.echo(f"OK petname set: {did_key} → {petname}")
    typer.echo(f"  saved: {st.path}")


@petname_app.command("list")
def cmd_petname_list(
    store: Optional[Path] = typer.Option(None, "--store"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """列全部 petname."""
    st = _petname_store(store)
    items = st.list_all()
    if json_output:
        typer.echo(json.dumps(items, ensure_ascii=False, indent=2))
        return
    if not items:
        typer.echo("(本地无 petname)")
        return
    typer.echo("| did | petname |")
    typer.echo("|---|---|")
    for did, name in sorted(items.items()):
        typer.echo(f"| {did} | {name} |")


@petname_app.command("rm")
def cmd_petname_rm(
    did_key: str = typer.Argument(..., help="目标 did"),
    store: Optional[Path] = typer.Option(None, "--store"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """删本地 petname."""
    st = _petname_store(store)
    removed = st.remove(did_key)
    if json_output:
        typer.echo(json.dumps({"did": did_key, "removed": removed}))
        return
    if removed:
        typer.echo(f"OK removed petname for {did_key}")
    else:
        typer.echo(f"(no petname for {did_key})")
        raise typer.Exit(code=1)


friend_app.add_typer(petname_app, name="petname")


# ── friend qr / qr-scan (P2-EF · QR 加朋友) ──────────────────────────────────

from sisoul.cli_commands.qr import cmd_qr as _cmd_qr  # noqa: E402
from sisoul.cli_commands.qr import cmd_qr_scan as _cmd_qr_scan  # noqa: E402

friend_app.command("qr")(_cmd_qr)
friend_app.command("qr-scan")(_cmd_qr_scan)


__all__ = ["friend_app"]
