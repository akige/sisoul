"""sisoul invite · 生成邀请文案 + QR + did link 给朋友分享.

输出 3 种格式:
- 文本邀请 (适合 IM / Slack / Discord 粘贴)
- QR PNG (适合截图分享)
- 短 URL (sisoul://invite?... deep link)
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Optional

import typer


invite_template_text = """\
你好! 想试试去中心化 P2P AI agent 协议吗?

我用的是 sisoul — 朋友圈互借 LLM、共享 case、Signal 级 chat, 0 服务器、Apache-2.0.

加我做朋友:
  did: {did}
  多地址: {multiaddr}
  petname (我建议): {petname}

装机 (alpha 期源码装, Python 3.11+):
  git clone https://github.com/akige/sisoul && cd sisoul
  python3.12 -m venv .venv && source .venv/bin/activate
  pip install -e '.[daemon,crypto,chat,llm]'

或试 PWA (0 装机):
  https://akige.github.io/sisoul/

装完跑:
  sisoul init --goals "..."
  sisoul friend add {did}  (加我)
  sisoul founder chat "为什么 sisoul 不发币?"

文档: https://github.com/akige/sisoul
"""


def _read_my_did_from_vault() -> Optional[str]:
    """Derive this user's did:key from vault seed.txt (BIP-39).

    Falls back to dna.json / identity.json if seed.txt missing and a did
    field is cached there.
    """
    import json, os
    vault = Path(os.environ.get("SISOUL_VAULT", str(Path.home() / ".sisoul"))).expanduser()
    seed_path = vault / "seed.txt"
    if seed_path.exists():
        try:
            from sisoul.identity import (
                load_mnemonic_from_file,
                mnemonic_to_master_key,
                generate_did_key_from_master,
            )
            mnemonic = load_mnemonic_from_file(seed_path)
            master = mnemonic_to_master_key(mnemonic)
            did, _priv, _pub = generate_did_key_from_master(master, index=0)
            return did
        except Exception:
            pass
    for candidate in (vault / "dna.json", vault / "identity.json", vault / "identity" / "dna.json"):
        if candidate.exists():
            try:
                d = json.loads(candidate.read_text())
                for k in ("did_key", "did", "didKey", "id"):
                    if d.get(k):
                        return d[k]
            except Exception:
                continue
    return None


def _read_my_petname_from_vault() -> Optional[str]:
    """Read this user's petname from vault wizard.json or dna.json."""
    import json, os
    vault = Path(os.environ.get("SISOUL_VAULT", str(Path.home() / ".sisoul"))).expanduser()
    for candidate in (vault / "wizard.json", vault / "dna.json"):
        if candidate.exists():
            try:
                d = json.loads(candidate.read_text())
                for k in ("petname", "name", "handle"):
                    if d.get(k):
                        return d[k]
            except Exception:
                continue
    return None


def cli_invite(
    did: Optional[str] = typer.Option(None, "--did", "-d", help="your did:key (default: read from vault)"),
    petname: Optional[str] = typer.Option(None, "--petname", "-p", help="your local petname (default: read from vault)"),
    multiaddr: str = typer.Option(
        "/ip4/127.0.0.1/tcp/4001/p2p/12D3KooWYourPeerId",
        "--multiaddr", "-m",
        help="your kubo multiaddr (sisoul net status to get)",
    ),
    output: Optional[Path] = typer.Option(
        None, "--out", "-o", help="write text invite to file (default stdout)"
    ),
    qr_out: Optional[Path] = typer.Option(
        None, "--qr-out", help="write QR PNG to file (requires qrcode package)"
    ),
    json_output: bool = typer.Option(False, "--json", "-j"),
    short_url: bool = typer.Option(False, "--short-url", help="print sisoul:// deep link"),
) -> None:
    """Generate friend invite (text / QR / sisoul:// link). Reads did/petname from vault by default."""
    if did is None:
        did = _read_my_did_from_vault()
        if did is None:
            typer.echo(
                "ERROR: no --did passed and no vault did found. "
                "Run `sisoul init` first, or pass --did explicitly.",
                err=True,
            )
            raise typer.Exit(code=1)
    if petname is None:
        petname = _read_my_petname_from_vault() or "alice"
    invite_text = invite_template_text.format(did=did, multiaddr=multiaddr, petname=petname)

    invite_payload = {
        "did": did,
        "petname_hint": petname,
        "multiaddr": multiaddr,
        "version": 1,
    }

    if json_output:
        typer.echo(json.dumps({
            "invite_text": invite_text,
            "invite_json": invite_payload,
            "short_url": f"sisoul://invite?{_query(invite_payload)}",
        }, ensure_ascii=False, indent=2))
        return

    if short_url:
        typer.echo(f"sisoul://invite?{_query(invite_payload)}")
        return

    if output:
        output.write_text(invite_text)
        typer.echo(f"OK invite written to {output}")
    else:
        typer.echo(invite_text)

    if qr_out:
        try:
            import qrcode
            img = qrcode.make(json.dumps(invite_payload))
            img.save(str(qr_out))
            typer.echo(f"\nOK QR written to {qr_out}")
        except ImportError:
            typer.echo("ERROR: qrcode package not installed (pip install qrcode[pil])", err=True)
            raise typer.Exit(code=1)


def _query(payload: dict) -> str:
    from urllib.parse import urlencode
    return urlencode({"d": payload["did"], "m": payload["multiaddr"], "p": payload["petname_hint"]})
