"""sisoul friend qr / qr-scan (P2-EF · QR 加朋友).

3 子命令:
- ``sisoul friend qr --out <path.png>``   生 PNG QR 含 JSON
    {"did": "did:key:z...", "multiaddr": "/ip4/...", "petname_hint": "Alice", "version": 1}
- ``sisoul friend qr --print``            终端 ASCII QR
- ``sisoul friend qr-scan <image-path>``  扫 QR 自动 friend add (pyzbar / cv2 fallback)

集成: cli_commands/friend.py 的 ``friend_app`` 在末尾 register 这两个命令.

设计取舍:
- 只用 ``qrcode`` (必装), 解码用 ``pyzbar`` / ``opencv-python`` (可选),
  都没装 fallback 手输 JSON.
- payload version 字段允许后续 schema 演进.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import typer


PAYLOAD_VERSION = 1


# ── payload helpers ──────────────────────────────────────────────────────────


def build_payload(
    did: str,
    multiaddr: str = "",
    petname_hint: str = "",
    version: int = PAYLOAD_VERSION,
) -> dict[str, Any]:
    """构造 QR 内嵌 JSON payload."""
    return {
        "did": did,
        "multiaddr": multiaddr,
        "petname_hint": petname_hint,
        "version": version,
    }


def parse_payload(raw: str) -> dict[str, Any]:
    """解析 QR JSON, 校验必要字段. 失败 ValueError."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"QR payload 不是合法 JSON: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("QR payload 顶层必须是 dict")
    did = data.get("did")
    if not isinstance(did, str) or not did.startswith("did:"):
        raise ValueError(f"QR payload.did 缺失或格式非法: {did!r}")
    if "version" in data and not isinstance(data["version"], int):
        raise ValueError(f"QR payload.version 必须是 int: {data['version']!r}")
    return data


# ── QR generation ────────────────────────────────────────────────────────────


def generate_qr_png(payload: dict[str, Any], out_path: Path) -> Path:
    """生 PNG QR. 返回写入路径.

    box_size=16 + ECC=L 选定理由: cv2.QRCodeDetector 对中等密度 + 小 box 解码不稳;
    box=16 像素出来 ~720x720 PNG, cv2/pyzbar 都能稳定解.
    """
    import qrcode

    out_path.parent.mkdir(parents=True, exist_ok=True)
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=16,
        border=4,
    )
    qr.add_data(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img.save(str(out_path))
    return out_path


def generate_qr_ascii(payload: dict[str, Any]) -> str:
    """生终端 ASCII QR (适合粘贴/截图)."""
    import io

    import qrcode

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        border=2,
    )
    qr.add_data(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    qr.make(fit=True)
    buf = io.StringIO()
    qr.print_ascii(out=buf, invert=True)
    return buf.getvalue()


# ── QR decoding (pyzbar / cv2 / fallback) ────────────────────────────────────


class QRDecodeError(Exception):
    """QR 解码失败 (库未装 / 图片不含 QR / 解码异常)."""


def decode_qr_image(image_path: Path) -> str:
    """尝试解 QR. 顺序: pyzbar → cv2 → raise QRDecodeError.

    返回 QR 内的字符串 payload (JSON 文本).
    """
    # 1) pyzbar
    try:
        from PIL import Image
        from pyzbar.pyzbar import decode as pyzbar_decode  # type: ignore[import-not-found]

        img = Image.open(str(image_path))
        results = pyzbar_decode(img)
        if results:
            return results[0].data.decode("utf-8")
        raise QRDecodeError(f"pyzbar 未在图片中找到 QR: {image_path}")
    except ImportError:
        pass  # 尝试 cv2

    # 2) opencv (含 upscale retry)
    try:
        import cv2  # type: ignore[import-not-found]

        img = cv2.imread(str(image_path))
        if img is None:
            raise QRDecodeError(f"cv2 读不开图片: {image_path}")
        detector = cv2.QRCodeDetector()
        for scale in (1.0, 2.0, 3.0):
            if scale != 1.0:
                resized = cv2.resize(
                    img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC
                )
            else:
                resized = img
            data, _points, _qr = detector.detectAndDecode(resized)
            if data:
                return data
        raise QRDecodeError(f"cv2 未在图片中找到 QR (含 1x/2x/3x): {image_path}")
    except ImportError:
        raise QRDecodeError(
            "解 QR 需装 pyzbar 或 opencv-python: "
            "pip install pyzbar  # macOS: brew install zbar  "
            "# 或 pip install opencv-python"
        )


# ── CLI handlers ─────────────────────────────────────────────────────────────


def _resolve_self_did(vault_dir: Optional[Path]) -> str:
    """从 vault 拿当前 did:key. 没 vault 则用占位."""
    root = vault_dir if vault_dir else Path.home() / ".sisoul"
    seed_path = root / "seed.txt"
    if not seed_path.exists():
        return "did:key:z6MkPLACEHOLDERnoVaultYetRunSisoulInitFirst"
    try:
        from sisoul.identity import generate_did_key, mnemonic_to_master_key

        mnemonic = seed_path.read_text(encoding="utf-8").strip()
        master_seed = mnemonic_to_master_key(mnemonic)
        # generate_did_key 返回字符串 did
        return generate_did_key(master_seed)
    except Exception:  # noqa: BLE001  — 兜底, 不阻断 qr 命令
        return "did:key:z6MkPLACEHOLDERvaultReadError"


def cmd_qr(
    out: Optional[Path] = typer.Option(
        None,
        "--out",
        help="PNG 输出路径 (例: ~/sisoul-qr.png). 跟 --print 互斥, 默认 --print.",
    ),
    print_ascii: bool = typer.Option(
        False, "--print", help="终端 ASCII QR (无 --out 时默认开)."
    ),
    did: Optional[str] = typer.Option(
        None, "--did", help="覆盖 did (默认从 vault seed 派生)."
    ),
    multiaddr: str = typer.Option(
        "", "--multiaddr", help="可选 libp2p multiaddr (例: /ip4/.../tcp/4001/p2p/...)"
    ),
    petname_hint: str = typer.Option(
        "", "--petname-hint", help="给朋友看的昵称提示 (本地化)"
    ),
    vault_dir: Optional[Path] = typer.Option(
        None, "--vault-dir", help="vault 目录 (默认 ~/.sisoul/)"
    ),
) -> None:
    """生 QR 给朋友扫加好友 (PNG 或终端 ASCII)."""
    actual_did = did if did else _resolve_self_did(vault_dir)
    payload = build_payload(
        did=actual_did, multiaddr=multiaddr, petname_hint=petname_hint
    )

    if out is None and not print_ascii:
        # 默认 ASCII 打印
        print_ascii = True

    if out is not None:
        path = generate_qr_png(payload, Path(out).expanduser())
        typer.echo(f"OK QR PNG 已写: {path}")
        typer.echo(f"   did: {actual_did}")
    if print_ascii:
        ascii_art = generate_qr_ascii(payload)
        typer.echo(ascii_art)
        typer.echo(f"did: {actual_did}")
        if multiaddr:
            typer.echo(f"multiaddr: {multiaddr}")
        if petname_hint:
            typer.echo(f"petname_hint: {petname_hint}")


def cmd_qr_scan(
    image_path: Path = typer.Argument(
        ..., help="QR 图片路径 (PNG/JPG, 含 sisoul QR payload)"
    ),
    nickname: str = typer.Option(
        "", "--nickname", "-n", help="本地昵称 (默认用 petname_hint)"
    ),
    vault_dir: Optional[Path] = typer.Option(
        None, "--vault-dir", help="vault 目录 (默认 ~/.sisoul/)"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="只解码 + 校验, 不真加 friend"
    ),
) -> None:
    """扫 QR 图片, 解 payload + 自动 friend add (失败 fallback 提示手输 JSON)."""
    image_path = Path(image_path).expanduser()
    if not image_path.exists():
        typer.echo(f"ERROR: 图片不存在: {image_path}", err=True)
        raise typer.Exit(code=1)

    raw: Optional[str] = None
    try:
        raw = decode_qr_image(image_path)
    except QRDecodeError as e:
        typer.echo(f"WARN: QR 解码失败 ({e})", err=True)
        typer.echo("→ Fallback: 请手动粘贴 QR 中 JSON payload (一行):", err=True)
        raw = typer.prompt("payload JSON")

    try:
        payload = parse_payload(raw or "")
    except ValueError as e:
        typer.echo(f"ERROR: payload 校验失败: {e}", err=True)
        raise typer.Exit(code=2)

    typer.echo(f"OK QR 解码成功: did={payload['did']}")
    if payload.get("multiaddr"):
        typer.echo(f"   multiaddr: {payload['multiaddr']}")
    if payload.get("petname_hint"):
        typer.echo(f"   petname_hint: {payload['petname_hint']}")
    typer.echo(f"   version: {payload.get('version', 'unknown')}")

    if dry_run:
        typer.echo("--dry-run: 不真加 friend.")
        return

    # 自动加 friend (复用 friend.py 的 _save_did_key_friends)
    try:
        from sisoul.cli_commands.friend import (
            _load_did_key_friends,
            _save_did_key_friends,
        )
        from sisoul.identity import decode_did_key
    except ImportError as e:
        typer.echo(f"ERROR: 无法 import friend 模块: {e}", err=True)
        raise typer.Exit(code=3)

    try:
        dk = decode_did_key(payload["did"])
    except Exception as e:  # noqa: BLE001
        typer.echo(f"ERROR: did:key decode 失败: {e}", err=True)
        raise typer.Exit(code=4)

    entries = _load_did_key_friends(vault_dir)
    final_nick = nickname or payload.get("petname_hint", "")
    from datetime import datetime, timezone

    record = {
        "did": dk.did,
        "pubkey_hex": dk.pubkey.hex(),
        "key_type": dk.key_type,
        "nickname": final_nick,
        "added_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "method": "did:key",
        "via": "qr-scan",
        "multiaddr": payload.get("multiaddr", ""),
    }
    existing_idx = None
    for i, e in enumerate(entries):
        if e.get("did") == dk.did:
            existing_idx = i
            break
    if existing_idx is not None:
        entries[existing_idx] = {**entries[existing_idx], **record}
        action = "updated"
    else:
        entries.append(record)
        action = "added"
    fp = _save_did_key_friends(entries, vault_dir)
    typer.echo(f"OK friend {action} via QR: {dk.did}")
    if final_nick:
        typer.echo(f"   nickname: {final_nick}")
    typer.echo(f"   saved: {fp}")


__all__ = [
    "PAYLOAD_VERSION",
    "QRDecodeError",
    "build_payload",
    "cmd_qr",
    "cmd_qr_scan",
    "decode_qr_image",
    "generate_qr_ascii",
    "generate_qr_png",
    "parse_payload",
]
