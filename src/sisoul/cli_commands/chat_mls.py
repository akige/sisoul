"""sisoul MLS group-chat CLI (RFC 9420 · L2 >2-person chat).

Subcommands::

    sisoul mls create <group> <did1> <did2> ...   create a group + persist state
    sisoul mls invite <group> <did>                add a member (emits commit+welcome)
    sisoul mls send   <group> "<message>"          group-AEAD encrypt (+publish)
    sisoul mls list                                 list local groups + epoch state

Group state (including epoch secrets) is persisted locally under the chat dir.
This is a skeleton: real deployments would wrap the blob with the at-rest
SecretBox used by 1:1 chat. ``send`` publishes to the GossipSub topic for the
group; pass ``--memory`` to use the in-process transport (tests / no daemon).
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import typer

from sisoul.chat.mls import MLSGroup, MLSGroupError
from sisoul.chat.mls_topic import MLSTopic, mls_topic_for
from sisoul.chat.session import chat_dir
from sisoul.chat.transport import (
    KuboGossipSubTransport,
    get_shared_memory_transport,
)

cli_mls = typer.Typer(
    name="mls",
    help="MLS group chat (RFC 9420) for >2 participants.",
    no_args_is_help=True,
)


# ---------------------------------------------------------------------------
# Local state helpers
# ---------------------------------------------------------------------------

def _mls_dir() -> Path:
    d = chat_dir() / "mls"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_name(group_id: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in group_id)[:80]


def _group_path(group_id: str) -> Path:
    return _mls_dir() / f"{_safe_name(group_id)}.json"


def _load_group(group_id: str) -> MLSGroup:
    path = _group_path(group_id)
    if not path.exists():
        raise typer.BadParameter(f"no local MLS group {group_id!r} (create it first)")
    return MLSGroup.from_state(path.read_bytes())


def _save_group(group: MLSGroup) -> None:
    _group_path(group.group_id).write_bytes(group.serialize_state())


def _resolve_local_did() -> str:
    env_did = os.environ.get("SISOUL_LOCAL_DID")
    if env_did:
        return env_did
    try:
        from sisoul.identity.did_key import list_dids  # type: ignore
        dids = list_dids()
        if dids:
            first = dids[0]
            return first if isinstance(first, str) else first.get("did", "did:key:zLocal")
    except Exception:
        pass
    return "did:key:zLocalSisoul"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@cli_mls.command("create")
def create(
    group: str = typer.Argument(..., help="group id (any string)"),
    members: list[str] = typer.Argument(..., help="member DIDs (did:key:z...)"),
) -> None:
    """Creates an MLS group with the given members and persists local state."""
    local = _resolve_local_did()
    roster = list(members)
    my_did = local if local in roster else roster[0]
    try:
        g = MLSGroup(group, roster, my_did=my_did)
    except MLSGroupError as exc:
        raise typer.BadParameter(str(exc))
    _save_group(g)
    typer.echo(json.dumps({
        "ok": True,
        "group_id": g.group_id,
        "members": g.members,
        "my_did": g.my_did,
        "epoch": g.epoch,
        "topic": mls_topic_for(g.group_id),
    }, ensure_ascii=False))


@cli_mls.command("invite")
def invite(
    group: str = typer.Argument(..., help="group id"),
    did: str = typer.Argument(..., help="DID to add"),
) -> None:
    """Adds a member: re-keys the group and emits the commit + welcome blobs."""
    g = _load_group(group)
    try:
        commit = g.add_member(did)
        welcome = g.create_welcome(did)
    except MLSGroupError as exc:
        raise typer.BadParameter(str(exc))
    _save_group(g)
    typer.echo(json.dumps({
        "ok": True,
        "group_id": g.group_id,
        "added": did,
        "epoch": g.epoch,
        "members": g.members,
        "commit_hex": commit.hex(),
        "welcome_hex": welcome.hex(),
    }, ensure_ascii=False))


@cli_mls.command("send")
def send(
    group: str = typer.Argument(..., help="group id"),
    message: str = typer.Argument(..., help="plaintext message"),
    memory: bool = typer.Option(False, "--memory", help="use in-process transport (test mode)"),
    publish: bool = typer.Option(True, "--publish/--no-publish", help="publish to GossipSub"),
) -> None:
    """Group-AEAD encrypts ``message`` and publishes it to the group topic."""
    g = _load_group(group)
    try:
        ciphertext = g.encrypt(message.encode())
    except MLSGroupError as exc:
        raise typer.BadParameter(str(exc))
    _save_group(g)  # persist advanced generation counter

    published = False
    if publish:
        transport = get_shared_memory_transport() if (
            memory or os.environ.get("SISOUL_CHAT_MEMORY") == "1"
        ) else KuboGossipSubTransport()
        topic = MLSTopic(g.group_id, transport)

        async def _pub() -> None:
            await topic.publish(ciphertext)
            await transport.close()

        try:
            asyncio.run(_pub())
            published = True
        except Exception as exc:  # daemon down etc. — still return ciphertext
            typer.echo(json.dumps({"warn": f"publish failed: {exc}"}), err=True)

    typer.echo(json.dumps({
        "ok": True,
        "group_id": g.group_id,
        "topic": mls_topic_for(g.group_id),
        "epoch": g.epoch,
        "ciphertext_hex": ciphertext.hex(),
        "published": published,
    }, ensure_ascii=False))


@cli_mls.command("list")
def list_groups() -> None:
    """Lists locally-known MLS groups and their epoch / membership state."""
    groups = []
    for path in sorted(_mls_dir().glob("*.json")):
        try:
            g = MLSGroup.from_state(path.read_bytes())
        except MLSGroupError:
            continue
        groups.append({
            "group_id": g.group_id,
            "epoch": g.epoch,
            "members": len(g.members),
            "my_did": g.my_did,
            "active": g._active,
            "topic": mls_topic_for(g.group_id),
        })
    typer.echo(json.dumps({"count": len(groups), "groups": groups}, ensure_ascii=False))


__all__ = ["cli_mls"]
