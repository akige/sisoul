"""sisoul did 命令组 (Phase 2 W21-W22, dev-B).

5 子命令:
- register <handle> [--network sepolia|mainnet|mock]  注册 DID + ENS subdomain
- resolve <did_or_ens>                                 查 DID 文档
- list                                                 本地已注册 DID
- link-friend <DID>                                    Phase 4 朋友关系 stub
- link-social <provider> [--token / --email]           Privy social recovery 关联

由 ``cli.py`` 主入口通过 ``app.add_typer(did_app, name='did')`` 整合.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from sisoul.identity.did import (
    DIDError,
    DIDNotFoundError,
    HandleAlreadyTakenError,
    InvalidHandleError,
    NetworkNotSupportedError,
    SocialRecoveryResult,
    link_friend_did,
    link_social_recovery,
    list_local_dids,
    register_did,
    resolve_did,
)
from sisoul.identity.did_key import (
    DidKeyError,
    generate_did_key_from_master,
)
from sisoul.identity.seed import (
    DEFAULT_SEED_FILE,
    InvalidMnemonicError,
    load_mnemonic_from_file,
    mnemonic_to_master_key,
)

did_app = typer.Typer(
    name="did",
    help="DID 链上身份 (Phase 2 W21-W22). ENS subdomain + Privy social recovery.",
    no_args_is_help=True,
)


def _registry_path_for(vault_dir: Path | None) -> Path | None:
    """vault_dir 指定时 → vault_dir/identity/dids.json; 否则 None (走默认 ~/.sisoul/)."""
    if vault_dir is None:
        return None
    return Path(vault_dir) / "identity" / "dids.json"


# ── did register ─────────────────────────────────────────────────────────────


@did_app.command("register")
def cmd_register(
    handle: str = typer.Argument(..., help="handle, 例 alice (→ alice.sisoul.eth)"),
    network: str = typer.Option(
        "sepolia",
        "--network",
        "-n",
        help="sepolia (默认, testnet) / mainnet (禁用) / mock (纯本地)",
    ),
    live: bool = typer.Option(
        False,
        "--live",
        help="真连 Sepolia RPC (只读 smoke, 不发 tx). 默认 mock 不连网.",
    ),
    vault_dir: Path = typer.Option(
        None, "--vault-dir", help="vault 路径 (默认 ~/.sisoul/)"
    ),
    rpc_url: str = typer.Option(
        None, "--rpc-url", help="自定义 Sepolia RPC (默认 env SISOUL_SEPOLIA_RPC 或公共 RPC)"
    ),
    json_output: bool = typer.Option(False, "--json", help="JSON 输出"),
) -> None:
    """注册 DID + ENS subdomain ``<handle>.sisoul.eth``."""
    try:
        did = register_did(
            handle,
            network=network,  # type: ignore[arg-type]
            registry_path=_registry_path_for(vault_dir),
            rpc_url=rpc_url,
            live=live,
        )
    except (InvalidHandleError, HandleAlreadyTakenError, NetworkNotSupportedError) as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=1)
    except DIDError as e:
        typer.echo(f"❌ DID 注册失败: {e}", err=True)
        raise typer.Exit(code=2)

    if json_output:
        typer.echo(json.dumps(did.to_dict(), ensure_ascii=False, indent=2))
        return

    typer.echo(f"✅ DID 已注册: {did.did_string}")
    typer.echo(f"   ENS subdomain: {did.ens_subdomain}")
    typer.echo(f"   网络: {did.network}")
    typer.echo(f"   public key: {did.public_key[:24]}...")
    if did.ens_tx_hash:
        typer.echo(f"   ENS tx: {did.ens_tx_hash}")
    else:
        typer.echo("   ENS tx: (live readonly mode, 未发 tx)")
    typer.echo("   提示: --network mainnet 当前禁用 (Phase 3 RC 开). --live 仅 readonly.")


# ── did resolve ──────────────────────────────────────────────────────────────


@did_app.command("resolve")
def cmd_resolve(
    target: str = typer.Argument(
        ..., help="did:sisoul:<handle> 或 <handle>.sisoul.eth"
    ),
    vault_dir: Path = typer.Option(
        None, "--vault-dir", help="vault 路径 (默认 ~/.sisoul/)"
    ),
    document: bool = typer.Option(
        False, "--document", "-d", help="输出完整 W3C DID Document JSON"
    ),
) -> None:
    """查 DID (本地 registry). Phase 3 加链上 fallback."""
    try:
        did = resolve_did(target, registry_path=_registry_path_for(vault_dir))
    except DIDNotFoundError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=1)
    except DIDError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=2)

    if document:
        typer.echo(json.dumps(did.to_did_document(), ensure_ascii=False, indent=2))
        return

    typer.echo(f"DID: {did.did_string}")
    typer.echo(f"ENS: {did.ens_subdomain}")
    typer.echo(f"network: {did.network}")
    typer.echo(f"public_key: {did.public_key}")
    typer.echo(f"controllers: {', '.join(did.controllers)}")
    if did.social_provider:
        typer.echo(f"social_recovery: {did.social_provider} (id={did.social_recovery_id})")
    typer.echo(f"created_at: {did.created_at}")


# ── did list ─────────────────────────────────────────────────────────────────


@did_app.command("list")
def cmd_list(
    vault_dir: Path = typer.Option(
        None, "--vault-dir", help="vault 路径 (默认 ~/.sisoul/)"
    ),
) -> None:
    """列本地已注册 DID."""
    dids = list_local_dids(registry_path=_registry_path_for(vault_dir))
    if not dids:
        typer.echo("(本地无已注册 DID. 运行 `sisoul did register <handle>`.)")
        return
    typer.echo("| handle | DID | ENS | network |")
    typer.echo("|---|---|---|---|")
    for d in dids:
        typer.echo(
            f"| {d.handle} | {d.did_string} | {d.ens_subdomain} | {d.network} |"
        )


# ── did link-friend ──────────────────────────────────────────────────────────


@did_app.command("link-friend")
def cmd_link_friend(
    friend_did: str = typer.Argument(
        ..., help="朋友 DID, 例 did:sisoul:alice 或 alice.sisoul.eth"
    ),
    own_handle: str = typer.Option(
        None,
        "--as",
        help="用本地哪个 handle 操作 (默认: registry 第一条)",
    ),
    vault_dir: Path = typer.Option(
        None, "--vault-dir", help="vault 路径 (默认 ~/.sisoul/)"
    ),
) -> None:
    """加朋友 (Phase 4 前置 stub, 不发 EAS attestation)."""
    registry = _registry_path_for(vault_dir)
    dids = list_local_dids(registry_path=registry)
    if not dids:
        typer.echo("❌ 本地无 DID, 先 `sisoul did register <handle>`", err=True)
        raise typer.Exit(code=1)

    if own_handle:
        matched = [d for d in dids if d.handle == own_handle]
        if not matched:
            typer.echo(f"❌ 本地无 handle={own_handle}", err=True)
            raise typer.Exit(code=1)
        own = matched[0]
    else:
        own = dids[0]

    try:
        rec = link_friend_did(own, friend_did, registry_path=registry)
    except DIDError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"✅ 朋友关系记录已加 (stub): {own.did_string} ↔ {friend_did}")
    typer.echo(f"   note: {rec['note']}")


# ── did link-social ──────────────────────────────────────────────────────────


@did_app.command("link-social")
def cmd_link_social(
    provider: str = typer.Argument(
        ..., help="social provider: github / google / apple / twitter / email"
    ),
    token: str = typer.Option(
        None, "--token", help="OAuth token (provider != email 必须)"
    ),
    email: str = typer.Option(
        None, "--email", help="email 地址 (provider=email 必须)"
    ),
    json_output: bool = typer.Option(False, "--json", help="JSON 输出"),
) -> None:
    """Privy social recovery 关联 (mock; Phase 3 RC 接真 SDK)."""
    try:
        result: SocialRecoveryResult = link_social_recovery(
            provider,  # type: ignore[arg-type]
            oauth_token=token,
            user_email=email,
        )
    except DIDError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=1)

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "provider": result.provider,
                    "user_id": result.user_id,
                    "embedded_wallet_address": result.embedded_wallet_address,
                    "issued_at": result.issued_at,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    typer.echo(f"✅ Privy social recovery 已关联 (mock)")
    typer.echo(f"   provider: {result.provider}")
    typer.echo(f"   Privy user id: {result.user_id}")
    typer.echo(f"   embedded wallet: {result.embedded_wallet_address}")
    typer.echo(f"   issued_at: {result.issued_at}")
    typer.echo("   提示: Phase 3 RC 才真调 Privy API, 当前为确定性 mock.")


# ── did show (Wave B' P0-3 · 显示本机 did:key) ───────────────────────────────


@did_app.command("show")
def cmd_show(
    seed_file: Path = typer.Option(
        None,
        "--seed-file",
        help=f"BIP-39 mnemonic 文件路径 (默认 {DEFAULT_SEED_FILE})",
    ),
    index: int = typer.Option(
        0, "--index", help="did:key 派生 index (默认 0)"
    ),
    passphrase: str = typer.Option("", "--passphrase", help="BIP-39 passphrase"),
    json_output: bool = typer.Option(False, "--json", help="JSON 输出"),
) -> None:
    """显示从 BIP-39 seed 派生的 did:key (Wave B' P0-3).

    无需链上注册, 同 seed + 同 index 跨设备一致.
    """
    try:
        mnemonic = load_mnemonic_from_file(seed_file)
    except FileNotFoundError:
        typer.echo(
            f"❌ seed 文件不存在 ({seed_file or DEFAULT_SEED_FILE}). "
            f"先 `sisoul init` 或 `sisoul restore-seed`.",
            err=True,
        )
        raise typer.Exit(code=1)
    except (InvalidMnemonicError, PermissionError) as e:
        typer.echo(f"❌ seed 文件无法加载: {e}", err=True)
        raise typer.Exit(code=1)

    try:
        master = mnemonic_to_master_key(mnemonic, passphrase=passphrase)
        did_key, _priv, pub = generate_did_key_from_master(master, index=index)
    except (InvalidMnemonicError, DidKeyError, ValueError) as e:
        typer.echo(f"❌ did:key 派生失败: {e}", err=True)
        raise typer.Exit(code=2)

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "did_key": did_key,
                    "pubkey_hex": pub.encode().hex(),
                    "index": index,
                    "key_type": "X25519",
                    "method": "did:key (W3C-CCG, local-only, no chain)",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    typer.echo(f"✅ 本机 did:key (index={index}):")
    typer.echo(f"   {did_key}")
    typer.echo(f"   pubkey (hex 32B): {pub.encode().hex()}")
    typer.echo("   key_type: X25519 (libsodium box 兼容)")
    typer.echo("   note: 完全本地派生, 跨设备同 seed 一致, 无需链上注册.")


__all__ = ["did_app"]
