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
    return ChatManager(
        local_did=_resolve_local_did(),
        master_key=_resolve_master_key(),
        transport=transport,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@chat_app.command("send")
def send(
    peer_did: str = typer.Argument(..., help="peer DID (did:key:z...)"),
    message: str = typer.Argument(..., help="plaintext message"),
    memory: bool = typer.Option(False, "--memory", help="use in-process transport (test mode)"),
) -> None:
    """Encrypts ``message`` and publishes to the chat topic for the peer."""
    mgr = _build_manager(memory)

    async def _run() -> None:
        await mgr.send(peer_did, message)
        mgr.persist()

    try:
        asyncio.run(_run())
    except RuntimeError as e:
        if "running event loop" in str(e) or "cannot be called" in str(e):
            # Already in a loop (e.g. typer + httpx async backend). Use loop policy.
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()
        else:
            raise
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


@chat_app.command("rotate-prekey")
def rotate_prekey(
    announce: bool = typer.Option(True, "--announce/--no-announce", help="publish new bundle"),
    memory: bool = typer.Option(False, "--memory"),
) -> None:
    """Generates a fresh PreKeyBundle and (optionally) announces it on GossipSub."""
    mgr = _build_manager(memory)
    bundle = mgr.rotate_prekey()

    async def _ann() -> str:
        return await mgr.announce_prekey()

    topic = asyncio.run(_ann()) if announce else prekey_topic_for(mgr.local_did)
    typer.echo(json.dumps({
        "ok": True,
        "did": mgr.local_did,
        "issued_at": bundle.issued_at,
        "mlkem_pub_len": len(bundle.mlkem_pub),
        "topic": topic,
        "announced": announce,
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


__all__ = ["chat_app"]
