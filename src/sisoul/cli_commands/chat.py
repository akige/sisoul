"""sisoul chat CLI (P2-G · Signal Double Ratchet + PQXDH).

Subcommands::

    sisoul chat send <peer-did> "<msg>"      encrypt + publish to GossipSub
    sisoul chat recv [--since 1h]             pull history + decrypt
    sisoul chat sessions list                 show active sessions + ratchet state
    sisoul chat rotate-prekey                 force refresh local pre-key bundle
    sisoul chat status                         show PQXDH mode + local DID

For tests/automation, ``--memory`` uses an in-process MemoryTransport instead
of the Kubo HTTP transport.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Optional

import typer

from sisoul.chat.pqxdh import pqxdh_mode
from sisoul.chat.session import ChatManager, chat_dir
from sisoul.chat.transport import (
    KuboGossipSubTransport,
    MemoryTransport,
    chat_topic_for,
    get_shared_memory_transport,
    prekey_topic_for,
)

chat_app = typer.Typer(
    name="chat",
    help="P2P end-to-end encrypted chat (Signal Double Ratchet + PQXDH).",
    no_args_is_help=True,
)

sessions_app = typer.Typer(name="sessions", help="Chat session introspection.", no_args_is_help=True)
chat_app.add_typer(sessions_app, name="sessions")


# ---------------------------------------------------------------------------
# Local config helpers
# ---------------------------------------------------------------------------

def _resolve_local_did() -> str:
    """Find local DID from did_key registry, fallback to env / random."""
    env_did = os.environ.get("SISOUL_LOCAL_DID")
    if env_did:
        return env_did
    try:
        from sisoul.identity.did_key import list_dids  # type: ignore
        dids = list_dids()
        if dids:
            # registry returns dicts with "did" key on most builds
            first = dids[0]
            return first if isinstance(first, str) else first.get("did", "did:key:zUnknown")
    except Exception:
        pass
    # Fallback: derive did:key from BIP-39 seed in vault (lightweight path)
    try:
        from pathlib import Path
        from sisoul.identity import (
            load_mnemonic_from_file,
            mnemonic_to_master_key,
            generate_did_key_from_master,
        )
        vault = Path(os.environ.get("SISOUL_VAULT", str(Path.home() / ".sisoul"))).expanduser()
        seed_path = vault / "seed.txt"
        if seed_path.exists():
            mnemonic = load_mnemonic_from_file(seed_path)
            master = mnemonic_to_master_key(mnemonic)
            did, _priv, _pub = generate_did_key_from_master(master, index=0)
            return did
    except Exception:
        pass
    return "did:key:zLocalSisoul"


def _resolve_master_key() -> bytes:
    """Master key for at-rest encryption — derived from identity seed if available."""
    try:
        from sisoul.identity.seed import load_master_key  # type: ignore
        mk = load_master_key()
        if mk:
            return mk[:32]
    except Exception:
        pass
    # Fallback: stable key from chat dir
    seed_path = chat_dir() / "master.key"
    if seed_path.exists():
        return seed_path.read_bytes()[:32]
    seed = os.urandom(32)
    seed_path.write_bytes(seed)
    return seed


def _build_transport(use_memory: bool) -> tuple[object, bool]:
    if use_memory or os.environ.get("SISOUL_CHAT_MEMORY") == "1":
        return get_shared_memory_transport(), True
    return KuboGossipSubTransport(), False


def _build_manager(use_memory: bool) -> ChatManager:
    transport, _ = _build_transport(use_memory)
    local_did = _resolve_local_did()
    master_key = _resolve_master_key()
    # Load persisted identity keys so daemon-announce / recv / send all share the
    # SAME prekey bundle (required for the GossipSub handshake to work across
    # processes); generate + persist on first use.
    from sisoul.chat.session import load_local_keys, save_local_keys

    keys = load_local_keys(local_did, master_key)
    if keys is None:
        from sisoul.chat.pqxdh import generate_pre_key_bundle

        keys = generate_pre_key_bundle(local_did)
        save_local_keys(keys, master_key)
    return ChatManager(
        local_did=local_did,
        master_key=master_key,
        transport=transport,  # type: ignore[arg-type]
        keys=keys,
    )


class _NoPrekey(Exception):
    """No prekey bundle could be discovered for the peer."""


def _run_async(coro):
    """Run a coroutine from the sync CLI, tolerating an already-running loop."""
    try:
        return asyncio.run(coro)
    except RuntimeError as e:
        if "running event loop" in str(e) or "cannot be called" in str(e):
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(coro)
            finally:
                loop.close()
        raise


def _bundle_from_dict(d: dict):
    """Reconstruct a PreKeyBundle from its to_dict() form (GossipSub or HTTP)."""
    from sisoul.chat.pqxdh import PreKeyBundle

    return PreKeyBundle(
        did=d["did"],
        x25519_pub=bytes.fromhex(d["x25519_pub"]),
        mlkem_pub=bytes.fromhex(d["mlkem_pub"]),
        signed_pre_key_pub=bytes.fromhex(d["signed_pre_key_pub"]),
        signature=bytes.fromhex(d["signature"]),
        issued_at=int(d["issued_at"]),
        pqxdh_mode=d.get("pqxdh_mode", "real"),
    )


async def _discover_prekey_gossipsub(mgr, peer_did: str, timeout: float) -> bool:
    """Subscribe to the peer's prekey GossipSub topic; cache the first match.

    Returns True if a bundle was cached. Requires the peer to be (re)announcing
    on GossipSub — their ``sisoul daemon`` does this periodically (A2)."""
    from sisoul.chat.transport import WireEnvelope, prekey_topic_for

    topic = prekey_topic_for(peer_did)
    try:
        gen = await mgr.transport.subscribe(topic)
    except Exception:  # noqa: BLE001
        return False

    async def _collect() -> bool:
        async for raw in gen:
            try:
                env = WireEnvelope.from_bytes(raw)
            except Exception:  # noqa: BLE001
                continue
            if env.kind != "prekey" or env.body.get("did") != peer_did:
                continue
            try:
                mgr.cache_peer_prekey(_bundle_from_dict(env.body))
                return True
            except Exception:  # noqa: BLE001
                continue
        return False

    try:
        return await asyncio.wait_for(_collect(), timeout=timeout)
    except asyncio.TimeoutError:
        return False
    finally:
        try:
            await gen.aclose()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@chat_app.command("send")
def send(
    peer_did: str = typer.Argument(..., help="peer DID (did:key:z...) or @username"),
    message: str = typer.Argument(..., help="plaintext message"),
    memory: bool = typer.Option(False, "--memory", help="use in-process transport (test mode)"),
    prekey_wait: float = typer.Option(
        8.0, "--prekey-wait", help="seconds to wait for the peer's prekey over GossipSub"
    ),
) -> None:
    """Encrypts ``message`` and publishes to the chat topic for the peer."""
    mgr = _build_manager(memory)

    # Telegram-style: accept @username (or bare username) and resolve to did via EAS.
    if not peer_did.startswith("did:"):
        handle = peer_did.lstrip("@")
        net = os.environ.get("SISOUL_USERNAME_NETWORK", "optimism-mainnet")
        try:
            from sisoul.onchain.username_eas import resolve_username as eas_resolve
            resolved = eas_resolve(handle, network=net)
        except Exception as e:  # noqa: BLE001
            typer.echo(f"ERROR: username resolve failed (EAS {net}): {e}", err=True)
            raise typer.Exit(code=2)
        if resolved is None:
            typer.echo(f"ERROR: @{handle} not registered on EAS ({net})", err=True)
            raise typer.Exit(code=1)
        peer_did = resolved

    async def _full_send() -> None:
        # Pre-key discovery is GossipSub-first (decentralised): subscribe to the
        # peer's prekey topic and catch a (re)announce — their daemon announces
        # periodically (A2). Fall back to an explicit self-hosted HTTP directory
        # ONLY if the user set SISOUL_PREKEY_DIRECTORY (there is no public default).
        if mgr.load_peer_prekey(peer_did) is None:
            got = await _discover_prekey_gossipsub(mgr, peer_did, prekey_wait)
            if not got and os.environ.get("SISOUL_PREKEY_DIRECTORY"):
                try:
                    from sisoul.prekey_directory import fetch_peer_prekey
                    bundle_dict = fetch_peer_prekey(peer_did)
                    if bundle_dict is not None:
                        mgr.cache_peer_prekey(_bundle_from_dict(bundle_dict))
                except Exception as fetch_err:  # noqa: BLE001
                    typer.echo(f"WARN: HTTP directory lookup failed: {fetch_err}", err=True)
            if mgr.load_peer_prekey(peer_did) is None:
                raise _NoPrekey()
        await mgr.send(peer_did, message)
        mgr.persist()

    try:
        _run_async(_full_send())
    except _NoPrekey:
        typer.echo(
            f"ERROR: no prekey bundle for {peer_did}. Ask them to run `sisoul daemon` "
            "(announces on GossipSub) then retry, or set SISOUL_PREKEY_DIRECTORY to a "
            "shared directory you both use.",
            err=True,
        )
        raise typer.Exit(code=1)

    typer.echo(json.dumps({
        "ok": True,
        "peer_did": peer_did,
        "topic": chat_topic_for(mgr.local_did, peer_did),
        "pqxdh_mode": pqxdh_mode(),
    }))


@chat_app.command("recv")
def recv(
    peer_did: Optional[str] = typer.Argument(None, help="peer DID; if omitted, listens all"),
    since: str = typer.Option("1h", "--since", help="window for history scan (informational)"),
    timeout: float = typer.Option(2.0, "--timeout", help="seconds to wait for messages"),
    memory: bool = typer.Option(False, "--memory", help="use in-process transport"),
) -> None:
    """Subscribes to the chat topic and prints decrypted messages."""
    mgr = _build_manager(memory)

    async def _run() -> list[dict]:
        from sisoul.chat.transport import WireEnvelope
        if peer_did is None:
            topics = [chat_topic_for(mgr.local_did, mgr.local_did)]
        else:
            topics = [chat_topic_for(mgr.local_did, peer_did)]
        out: list[dict] = []
        for topic in topics:
            gen = await mgr.transport.subscribe(topic)
            try:
                async def _collect() -> None:
                    async for raw in gen:
                        try:
                            env = WireEnvelope.from_bytes(raw)
                            result = await mgr.handle_incoming(env)
                            if result is not None:
                                pd, pt = result
                                out.append({"from_did": pd, "plaintext": pt.decode(errors="replace")})
                        except Exception as exc:
                            out.append({"error": str(exc)})

                try:
                    await asyncio.wait_for(_collect(), timeout=timeout)
                except asyncio.TimeoutError:
                    pass
            finally:
                try:
                    await gen.aclose()  # type: ignore[attr-defined]
                except Exception:
                    pass
        return out

    messages = asyncio.run(_run())
    typer.echo(json.dumps({
        "since": since,
        "count": len(messages),
        "messages": messages,
    }, ensure_ascii=False))


@sessions_app.command("list")
def sessions_list(
    memory: bool = typer.Option(False, "--memory"),
) -> None:
    """Lists active chat sessions and their ratchet counters."""
    mgr = _build_manager(memory)
    sessions = mgr.list_sessions()
    typer.echo(json.dumps({
        "local_did": mgr.local_did,
        "pqxdh_mode": pqxdh_mode(),
        "sessions": sessions,
    }, ensure_ascii=False))


@chat_app.command("inbox")
def cmd_inbox(
    since_hours: float = typer.Option(168.0, "--since-hours",
        help="show envelopes newer than this many hours (default: 7 days)"),
    limit: int = typer.Option(50, "--limit", "-n"),
    json_output: bool = typer.Option(False, "--json", "-j"),
    memory: bool = typer.Option(False, "--memory"),
) -> None:
    """List inbound chat hints from an OPTIONAL self-hosted prekey directory.

    Decentralised live messages arrive via `sisoul chat recv` (GossipSub). This
    inbox is only the optional self-hosted-directory path; set SISOUL_PREKEY_DIRECTORY
    to use it. Without it, this is a no-op pointing you at `recv`."""
    import time
    mgr = _build_manager(memory)
    if not os.environ.get("SISOUL_PREKEY_DIRECTORY"):
        if json_output:
            typer.echo(json.dumps({
                "did": mgr.local_did, "entries": [],
                "hint": "no SISOUL_PREKEY_DIRECTORY set; use `sisoul chat recv` for live P2P messages",
            }))
        else:
            typer.echo("(no SISOUL_PREKEY_DIRECTORY set — inbox is the optional self-hosted path)")
            typer.echo("For decentralised live messages run:  sisoul chat recv")
        return
    from sisoul.prekey_directory import list_inbox, PrekeyDirectoryError
    since = time.time() - since_hours * 3600.0
    try:
        entries = list_inbox(mgr.local_did, since=since, limit=limit)
    except PrekeyDirectoryError as e:
        typer.echo(f"ERROR: prekey directory unreachable: {e}", err=True)
        raise typer.Exit(code=2)
    if json_output:
        typer.echo(json.dumps({"did": mgr.local_did, "entries": entries}, indent=2))
        return
    if not entries:
        typer.echo(f"(no inbox entries in the last {since_hours:.0f}h)")
        return
    typer.echo(f"Inbox for {mgr.local_did} ({len(entries)} entries):")
    typer.echo(f"{'when':<16}{'kind':<16}{'from':<25}{'note'}")
    typer.echo("-" * 90)
    for e in entries:
        age_h = (time.time() - e.get("received_at", 0)) / 3600
        sender = e.get("sender_did", "?")
        sender_short = sender[:22] + "..." if len(sender) > 25 else sender
        when = f"{age_h:.1f}h ago" if age_h < 24 else f"{age_h/24:.1f}d ago"
        kind = e.get("kind", "?")
        note = e.get("note", "")
        typer.echo(f"{when:<16}{kind:<16}{sender_short:<25}{note}")


@chat_app.command("rotate-prekey")
def rotate_prekey(
    announce: bool = typer.Option(True, "--announce/--no-announce", help="publish new bundle"),
    memory: bool = typer.Option(False, "--memory"),
) -> None:
    """Generates a fresh PreKeyBundle and (optionally) announces it on GossipSub."""
    mgr = _build_manager(memory)
    bundle = mgr.rotate_prekey()
    # Persist the rotated keys so the daemon (announce) + recv use the same bundle.
    try:
        from sisoul.chat.session import save_local_keys
        save_local_keys(mgr.keys, _resolve_master_key())
    except Exception:  # noqa: BLE001
        pass

    async def _ann() -> str:
        return await mgr.announce_prekey()

    topic = _run_async(_ann()) if announce else prekey_topic_for(mgr.local_did)

    # Optional: also publish to a self-hosted HTTP directory if the user set one.
    published_to_directory = False
    directory_error: Optional[str] = None
    if os.environ.get("SISOUL_PREKEY_DIRECTORY"):
        try:
            from sisoul.prekey_directory import publish_my_prekey
            publish_my_prekey(mgr.local_did, bundle.to_dict())
            published_to_directory = True
        except Exception as e:  # noqa: BLE001
            directory_error = f"{type(e).__name__}: {e}"

    typer.echo(json.dumps({
        "ok": True,
        "did": mgr.local_did,
        "issued_at": bundle.issued_at,
        "mlkem_pub_len": len(bundle.mlkem_pub),
        "topic": topic,
        "announced_gossipsub": announce,
        "published_directory": published_to_directory,
        "directory_error": directory_error,
        "pqxdh_mode": pqxdh_mode(),
    }))


@chat_app.command("status")
def status() -> None:
    """Reports PQXDH backend mode, local DID, and chat dir."""
    typer.echo(json.dumps({
        "pqxdh_mode": pqxdh_mode(),
        "local_did": _resolve_local_did(),
        "chat_dir": str(chat_dir()),
    }, ensure_ascii=False))


@chat_app.command("export-prekey")
def cmd_export_prekey(
    out: Optional[Path] = typer.Option(None, "--out", "-o",
        help="write bundle JSON to file (default stdout)"),
    memory: bool = typer.Option(False, "--memory"),
) -> None:
    """Export your local PQXDH PreKey bundle as JSON so a friend can import it.

    Alpha v1.0 manual bundle exchange (GossipSub auto-discovery is v1.1).
    Run this on your machine, send the file to your friend out-of-band,
    they run `sisoul chat cache-peer <file>` to install it.
    """
    mgr = _build_manager(memory)
    bundle = mgr.rotate_prekey()
    payload = bundle.to_dict()
    payload["version"] = 1
    if out:
        out.write_text(json.dumps(payload, indent=2))
        typer.echo(f"OK PreKey bundle exported to {out}")
        typer.echo(f"  did: {mgr.local_did}")
        typer.echo(f"  send {out} to your friend; they run `sisoul chat cache-peer {out}`")
    else:
        typer.echo(json.dumps(payload, indent=2))


@chat_app.command("cache-peer")
def cmd_cache_peer(
    bundle_file: Path = typer.Argument(..., help="path to peer's bundle JSON (from sisoul chat export-prekey)"),
    memory: bool = typer.Option(False, "--memory"),
) -> None:
    """Import a friend's PreKey bundle so you can chat with them."""
    from sisoul.chat.pqxdh import PreKeyBundle
    try:
        payload = json.loads(bundle_file.read_text())
    except (OSError, json.JSONDecodeError) as e:
        typer.echo(f"ERROR: failed to read {bundle_file}: {e}", err=True)
        raise typer.Exit(code=1)
    try:
        bundle = PreKeyBundle(
            did=payload["did"],
            x25519_pub=bytes.fromhex(payload["x25519_pub"]),
            mlkem_pub=bytes.fromhex(payload["mlkem_pub"]),
            signed_pre_key_pub=bytes.fromhex(payload["signed_pre_key_pub"]),
            signature=bytes.fromhex(payload["signature"]),
            issued_at=int(payload["issued_at"]),
            pqxdh_mode=payload.get("pqxdh_mode", "real"),
        )
    except (KeyError, ValueError) as e:
        typer.echo(f"ERROR: bundle invalid: {e}", err=True)
        raise typer.Exit(code=1)
    mgr = _build_manager(memory)
    mgr.cache_peer_prekey(bundle)
    mgr.persist()
    typer.echo(f"OK cached PreKey bundle for {bundle.did}")
    typer.echo(f"  You can now `sisoul chat send {bundle.did} 'hi'`")


__all__ = ["chat_app"]
