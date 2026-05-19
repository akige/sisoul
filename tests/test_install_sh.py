"""Wave A #11 — tests for ops/release/install.sh + sigstore tooling.

Covers:
  1. install.sh --help works (POSIX sh syntax check)
  2. install.sh --dry-run prints expected URLs without writing
  3. install.sh aborts on unknown arg (exit 2)
  4. install.sh detects platform (darwin/arm64 or linux/x86_64)
  5. install.sh version override via --version flag
  6. install.sh sets --no-verify path correctly (skip cosign)
  7. install.sh fails when binary download 404s
  8. sigstore_sign.sh round-trip: build → sign → verify-blob "Verified OK"
  9. verify.sh fails on tampered binary (reverse case, §J-2 §3)
 10. install.sh end-to-end with local HTTP mirror: real cosign verify + install
     to fresh dest + sisoul --version returns "sisoul 1.0.0+internal"

These tests run cosign + a real PyInstaller-built binary when present; they
skip gracefully when prerequisites are missing so CI can run them in
slimmer environments.
"""

from __future__ import annotations

import http.server
import os
import shutil
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
OPS_REL = REPO / "ops" / "release"
INSTALL_SH = OPS_REL / "install.sh"
SIGN_SH = OPS_REL / "sigstore_sign.sh"
VERIFY_SH = OPS_REL / "verify.sh"
BUILD_SH = OPS_REL / "build-binary.sh"


# ---------- helpers ----------

def have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _SilentHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass


def _serve(directory: Path):
    """Spawn a tiny HTTP server in a background thread, return (port, stop_fn)."""
    port = free_port()
    handler = lambda *a, **kw: _SilentHandler(*a, directory=str(directory), **kw)
    server = socketserver.TCPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def stop():
        server.shutdown()
        server.server_close()

    return port, stop


def _dist_binary() -> Path | None:
    """Return the path to a pre-built binary if one exists, else None."""
    dist = OPS_REL / "dist"
    if not dist.exists():
        return None
    cands = list(dist.glob("sisoul-*-darwin-arm64")) + list(
        dist.glob("sisoul-*-linux-*")
    )
    # Filter out .sig / .bundle / .sha256 by checking the trailing arch suffix
    cands = [
        c for c in cands
        if c.is_file()
        and (c.name.endswith(("-darwin-arm64", "-darwin-x86_64",
                              "-linux-x86_64", "-linux-arm64")))
    ]
    return cands[0] if cands else None


# ---------- tests ----------

class TestInstallShSyntax:
    """1, 2, 3, 4, 5, 6 — basic install.sh contract."""

    def test_01_sh_syntax_check(self):
        """install.sh must be POSIX sh compatible."""
        r = subprocess.run(["sh", "-n", str(INSTALL_SH)], capture_output=True)
        assert r.returncode == 0, r.stderr.decode()

    def test_02_help_flag(self):
        r = subprocess.run(
            ["sh", str(INSTALL_SH), "--help"], capture_output=True, text=True
        )
        assert r.returncode == 0
        assert "sisoul-cli one-line installer" in r.stdout

    def test_03_unknown_arg_exits_2(self):
        r = subprocess.run(
            ["sh", str(INSTALL_SH), "--bogus"], capture_output=True, text=True
        )
        assert r.returncode == 2
        assert "unknown arg" in r.stderr

    def test_04_dry_run_prints_urls_no_write(self, tmp_path):
        dest = tmp_path / ".local" / "bin"
        env = {
            **os.environ,
            "SISOUL_DRY_RUN": "1",
            "SISOUL_DEST": str(dest),
        }
        r = subprocess.run(
            ["sh", str(INSTALL_SH), "--version", "1.0.0+internal"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert r.returncode == 0
        out = r.stderr + r.stdout
        assert "[dry-run]" in out
        assert "v1.0.0+internal" in out
        assert "darwin" in out or "linux" in out
        # No file written
        assert not dest.exists()

    def test_05_version_override_via_flag(self, tmp_path):
        env = {**os.environ, "SISOUL_DRY_RUN": "1"}
        r = subprocess.run(
            ["sh", str(INSTALL_SH), "--version", "9.9.9-test"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert r.returncode == 0
        assert "v9.9.9-test" in (r.stderr + r.stdout)

    def test_06_no_verify_flag_parses(self, tmp_path):
        env = {**os.environ, "SISOUL_DRY_RUN": "1"}
        r = subprocess.run(
            ["sh", str(INSTALL_SH), "--no-verify", "--version", "1.0.0+internal"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert r.returncode == 0


class TestInstallShDownload:
    """7 — download failure handling."""

    def test_07_404_download_fails(self, tmp_path):
        """Binary URL 404 → install.sh exits 1, no binary installed."""
        if not have("curl"):
            pytest.skip("curl not available")
        # Empty HTTP server (no files), so every download 404s
        served = tmp_path / "served"
        served.mkdir()
        port, stop = _serve(served)
        try:
            dest = tmp_path / "bin"
            env = {
                **os.environ,
                "SISOUL_GH_BASE": f"http://127.0.0.1:{port}",
                "SISOUL_PUBKEY_URL": f"http://127.0.0.1:{port}/cosign.pub",
                "SISOUL_DEST": str(dest),
                # Skip IPFS fallback
                "SISOUL_IPFS_CID": "",
            }
            r = subprocess.run(
                ["sh", str(INSTALL_SH), "--version", "1.0.0+internal"],
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )
            assert r.returncode != 0
            assert not (dest / "sisoul").exists()
        finally:
            stop()


class TestSigstoreRoundtrip:
    """8 — full build → sign → verify chain."""

    @pytest.fixture(scope="class")
    def signed_binary(self):
        binary = _dist_binary()
        if binary is None:
            pytest.skip("no pre-built binary in ops/release/dist/; run build-binary.sh first")
        if not have("cosign"):
            pytest.skip("cosign not in PATH")
        # Ensure sign artifacts exist; re-sign if missing
        bundle = binary.with_name(binary.name + ".bundle")
        if not bundle.exists():
            r = subprocess.run(
                ["bash", str(SIGN_SH), str(binary)],
                capture_output=True,
                text=True,
            )
            assert r.returncode == 0, r.stderr
        return binary

    def test_08_verify_blob_says_verified_ok(self, signed_binary):
        r = subprocess.run(
            ["bash", str(VERIFY_SH), str(signed_binary)],
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, r.stderr
        # Real verification flow MUST emit "Verified OK"
        assert "Verified OK" in (r.stdout + r.stderr)


class TestReverseTamper:
    """9 — reverse case: tampered binary fails verify (§J-2)."""

    def test_09_tampered_binary_fails_verify(self, tmp_path):
        binary = _dist_binary()
        if binary is None:
            pytest.skip("no pre-built binary; skip")
        if not have("cosign"):
            pytest.skip("cosign not in PATH")
        # Copy + corrupt
        bad = tmp_path / binary.name
        shutil.copy(binary, bad)
        with open(bad, "ab") as f:
            f.write(b"\x00\xFFEVILTAMPER\x00")
        # Bundle is the unmodified one
        shutil.copy(binary.with_name(binary.name + ".bundle"),
                    bad.with_name(bad.name + ".bundle"))
        r = subprocess.run(
            ["bash", str(VERIFY_SH), str(bad)],
            capture_output=True,
            text=True,
        )
        assert r.returncode != 0
        assert "FAILED" in (r.stdout + r.stderr) or "invalid signature" in (r.stdout + r.stderr)


class TestEndToEnd:
    """10 — real install to fresh dir with local mirror."""

    def test_10_real_install_fresh_dir_verifies_and_runs(self, tmp_path):
        binary = _dist_binary()
        if binary is None:
            pytest.skip("no pre-built binary; run build-binary.sh first")
        if not have("cosign") or not have("curl"):
            pytest.skip("cosign/curl missing")
        bundle = binary.with_name(binary.name + ".bundle")
        pubkey = OPS_REL / "cosign.pub"
        for p in (bundle, pubkey):
            if not p.exists():
                pytest.skip(f"missing artifact: {p}")

        # Mirror release layout
        mirror = tmp_path / "mirror"
        (mirror / "v1.0.0+internal").mkdir(parents=True)
        shutil.copy(binary, mirror / "v1.0.0+internal" / binary.name)
        shutil.copy(bundle, mirror / "v1.0.0+internal" / bundle.name)
        shutil.copy(pubkey, mirror / "cosign.pub")

        port, stop = _serve(mirror)
        try:
            dest = tmp_path / "fresh-cache" / ".local" / "bin"
            env = {
                **os.environ,
                "SISOUL_GH_BASE": f"http://127.0.0.1:{port}",
                "SISOUL_PUBKEY_URL": f"http://127.0.0.1:{port}/cosign.pub",
                "SISOUL_DEST": str(dest),
                "SISOUL_INSECURE_IGNORE_TLOG": "1",
            }
            r = subprocess.run(
                ["sh", str(INSTALL_SH), "--version", "1.0.0+internal"],
                capture_output=True,
                text=True,
                env=env,
                timeout=120,
            )
            assert r.returncode == 0, r.stderr
            installed = dest / "sisoul"
            assert installed.exists() and os.access(installed, os.X_OK)
            # Real --version sanity
            v = subprocess.run([str(installed), "--version"],
                               capture_output=True, text=True)
            assert v.returncode == 0
            assert v.stdout.startswith("sisoul 1.0.0+internal")
            # And "Verified OK" must have appeared
            assert "Verified OK" in (r.stdout + r.stderr)
        finally:
            stop()
