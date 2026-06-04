"""tests/test_qr.py — P2-EF QR (friend qr / qr-scan) 单测.

覆盖:
- build_payload / parse_payload  (valid + invalid)
- generate_qr_png 写文件
- generate_qr_ascii 含 QR 标志字符
- 解码 roundtrip (生 PNG → cv2 / pyzbar 解 → parse_payload)
- CLI: `sisoul friend qr --out ...` 写 PNG
- CLI: `sisoul friend qr-scan <png>` 加 did:key friend (dry-run + 真加)
- 校验 invalid JSON payload
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sisoul.cli import app
from sisoul.cli_commands.qr import (
    PAYLOAD_VERSION,
    build_payload,
    generate_qr_ascii,
    generate_qr_png,
    parse_payload,
)

runner = CliRunner()


# ── payload helpers ──────────────────────────────────────────────────────────


def test_build_payload_default_version() -> None:
    p = build_payload(did="did:key:z6MkABC", multiaddr="/ip4/1.2.3.4", petname_hint="Alice")
    assert p["did"] == "did:key:z6MkABC"
    assert p["multiaddr"] == "/ip4/1.2.3.4"
    assert p["petname_hint"] == "Alice"
    assert p["version"] == PAYLOAD_VERSION


def test_parse_payload_valid_roundtrip() -> None:
    p = build_payload(did="did:key:z6MkXYZ")
    raw = json.dumps(p)
    parsed = parse_payload(raw)
    assert parsed["did"] == "did:key:z6MkXYZ"
    assert parsed["version"] == PAYLOAD_VERSION


def test_parse_payload_rejects_bad_json() -> None:
    with pytest.raises(ValueError, match="不是合法 JSON"):
        parse_payload("{not-json")


def test_parse_payload_rejects_missing_did() -> None:
    with pytest.raises(ValueError, match="did"):
        parse_payload(json.dumps({"multiaddr": "/ip4/...", "version": 1}))


def test_parse_payload_rejects_non_did_prefix() -> None:
    with pytest.raises(ValueError, match="did"):
        parse_payload(json.dumps({"did": "not-a-did:foo", "version": 1}))


def test_parse_payload_rejects_non_dict() -> None:
    with pytest.raises(ValueError, match="顶层"):
        parse_payload(json.dumps(["did:key:z6MkABC"]))


# ── PNG / ASCII generation ───────────────────────────────────────────────────


def test_generate_qr_png_writes_file(tmp_path: Path) -> None:
    out = tmp_path / "qr.png"
    payload = build_payload(did="did:key:z6MkPNGTEST")
    written = generate_qr_png(payload, out)
    assert written == out
    assert out.exists()
    assert out.stat().st_size > 100  # PNG > 100 字节
    # PNG magic
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_generate_qr_ascii_contains_qr_chars() -> None:
    payload = build_payload(did="did:key:z6MkASCII")
    art = generate_qr_ascii(payload)
    # qrcode 的 print_ascii 用半块 / 全块 unicode 字符
    assert any(ch in art for ch in ("█", "▀", "▄", " "))
    assert len(art) > 50


# ── 解码 roundtrip (用 opencv 或 pyzbar; 装失败 skip) ───────────────────────


def _can_decode() -> bool:
    try:
        import cv2  # noqa: F401

        return True
    except ImportError:
        pass
    try:
        from pyzbar.pyzbar import decode as _d  # noqa: F401

        return True
    except ImportError:
        return False


@pytest.mark.skipif(not _can_decode(), reason="未装 cv2 / pyzbar")
def test_qr_decode_roundtrip(tmp_path: Path) -> None:
    from sisoul.cli_commands.qr import decode_qr_image

    payload_in = build_payload(
        did="did:key:z6MkRoundTrip", multiaddr="/ip4/10.0.0.1", petname_hint="Bob"
    )
    out = tmp_path / "rt.png"
    generate_qr_png(payload_in, out)
    raw = decode_qr_image(out)
    payload_out = parse_payload(raw)
    assert payload_out["did"] == payload_in["did"]
    assert payload_out["multiaddr"] == payload_in["multiaddr"]
    assert payload_out["petname_hint"] == payload_in["petname_hint"]


# ── CLI integration ──────────────────────────────────────────────────────────


def test_cli_qr_writes_png(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    out = tmp_path / "out.png"
    r = runner.invoke(
        app,
        [
            "friend",
            "qr",
            "--did",
            "did:key:z6MkCLI",
            "--petname-hint",
            "Cli",
            "--out",
            str(out),
            "--vault-dir",
            str(vault),
        ],
    )
    assert r.exit_code == 0, r.output
    assert "QR PNG 已写" in r.output
    assert out.exists()
    assert out.stat().st_size > 100


def test_cli_qr_ascii_print(tmp_path: Path) -> None:
    r = runner.invoke(
        app,
        ["friend", "qr", "--print", "--did", "did:key:z6MkPRT"],
    )
    assert r.exit_code == 0, r.output
    # ASCII QR 输出含 did 行
    assert "did:key:z6MkPRT" in r.output


@pytest.mark.skipif(not _can_decode(), reason="未装 cv2 / pyzbar")
def test_cli_qr_scan_adds_friend(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    # 用 sisoul 的 generate_did_key 造一个合法 did
    from sisoul.identity import generate_did_key

    real_did = generate_did_key(os.urandom(32))
    payload = build_payload(did=real_did, petname_hint="ScanFriend")
    qr_path = tmp_path / "scan.png"
    generate_qr_png(payload, qr_path)

    r = runner.invoke(
        app,
        [
            "friend",
            "qr-scan",
            str(qr_path),
            "--vault-dir",
            str(vault),
            "--nickname",
            "ScannedBob",
        ],
    )
    assert r.exit_code == 0, r.output
    assert "QR 解码成功" in r.output
    assert "friend" in r.output and "via QR" in r.output

    friends_file = vault / "identity" / "didkey_friends.json"
    assert friends_file.exists()
    friends = json.loads(friends_file.read_text())
    assert any(f["did"] == real_did for f in friends)
    assert any(f.get("nickname") == "ScannedBob" for f in friends)


def test_cli_qr_scan_dry_run(tmp_path: Path) -> None:
    """dry-run 不真加 friend, 即使无解码库装好也可走 fallback (但要 cv2/pyzbar 否则 prompt)."""
    if not _can_decode():
        pytest.skip("无解码库")
    vault = tmp_path / "vault"
    vault.mkdir()
    from sisoul.identity import generate_did_key

    real_did = generate_did_key(os.urandom(32))
    payload = build_payload(did=real_did)
    qr_path = tmp_path / "dry.png"
    generate_qr_png(payload, qr_path)

    r = runner.invoke(
        app,
        ["friend", "qr-scan", str(qr_path), "--vault-dir", str(vault), "--dry-run"],
    )
    assert r.exit_code == 0, r.output
    assert "dry-run" in r.output
    # dry-run 不写文件
    friends_file = vault / "identity" / "didkey_friends.json"
    assert not friends_file.exists()
