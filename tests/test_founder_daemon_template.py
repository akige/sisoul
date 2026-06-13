"""#13 — sisoul-founder-daemon.service must be a placeholder template.

The unit file ships with __USER__ / __HOME__ placeholders (no hardcoded user
or home path), and install-founder-daemon.sh renders them for the invoking
user. Locks in: no machine-specific paths leak into the public repo.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SERVICE = REPO / "ops" / "init" / "sisoul-founder-daemon.service"
INSTALLER = REPO / "ops" / "init" / "install-founder-daemon.sh"


def test_service_template_exists():
    assert SERVICE.is_file(), f"missing template: {SERVICE}"
    assert INSTALLER.is_file(), f"missing installer: {INSTALLER}"


def test_service_uses_placeholders_not_hardcoded_paths():
    text = SERVICE.read_text()
    # placeholders present on the real directives
    assert "User=__USER__" in text
    assert "__HOME__/sisoul-dev" in text
    # no hardcoded home / user paths anywhere
    assert "/home/" not in text, "hardcoded /home/<user> path in template"
    assert "/Users/" not in text, "hardcoded macOS /Users/<user> path in template"
    # no leftover hardcoded literal username on a directive line
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        assert "YOUR_USER" not in line, "leftover YOUR_USER on a non-comment line"


def test_installer_is_executable():
    mode = INSTALLER.stat().st_mode
    assert mode & 0o111, "install-founder-daemon.sh not executable"


def test_installer_dry_run_renders_placeholders():
    """DRY_RUN render substitutes __USER__/__HOME__ and emits valid unit body."""
    env = dict(os.environ, DRY_RUN="1")
    proc = subprocess.run(
        ["bash", str(INSTALLER)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    # placeholders must be gone in the rendered output
    assert "__USER__" not in out
    assert "__HOME__" not in out
    # rendered output must still be a systemd unit
    assert "[Service]" in out and "ExecStart=" in out
    # User= directive now has a concrete value
    assert re.search(r"^User=\S+", out, re.M)


def test_installer_rejects_non_linux(monkeypatch, tmp_path):
    """On a non-Linux uname the installer aborts (launchd plist path instead)."""
    # Fake `uname` returning Darwin via a shim on PATH.
    shim = tmp_path / "uname"
    shim.write_text("#!/bin/sh\necho Darwin\n")
    shim.chmod(0o755)
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}", DRY_RUN="1")
    proc = subprocess.run(
        ["bash", str(INSTALLER)], capture_output=True, text=True, env=env, timeout=30
    )
    assert proc.returncode != 0
    assert "Linux/systemd only" in proc.stderr
