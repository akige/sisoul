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

装机 (一行):
  curl -sSfL https://github.com/sisoul/sisoul/releases/latest/download/install.sh | bash

或试 PWA (0 装机):
  https://sisoul.github.io/sisoul-pwa/

装完跑:
  sisoul init  (5-step wizard)
  sisoul friend add {did}  (加我)
  sisoul ask "Hello world"

想看快速 demo?
  sisoul daemon start --background && sisoul demo

文档: https://github.com/sisoul/sisoul
"""


def cli_invite(
    did: str = typer.Option(..., "--did", "-d", help="your did:key"),
    petname: str = typer.Option(..., "--petname", "-p", help="your local petname"),
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
    """Generate friend invite (text / QR / sisoul:// link)."""
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
