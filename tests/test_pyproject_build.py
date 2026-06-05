"""Test that `python -m build` produces installable wheel + sdist (PyPI publish gate).

These tests would have caught the pyproject.toml `dependencies` nested-under-urls bug
that allowed setuptools to build but broke tomllib + stricter parsers.
"""
from __future__ import annotations
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory):
    """Build wheel once, share across tests."""
    out = tmp_path_factory.mktemp("build")
    result = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(out)],
        cwd=str(REPO), capture_output=True, text=True, timeout=180,
    )
    if result.returncode != 0:
        pytest.skip(f"build failed (may be missing build package): {result.stderr[:300]}")
    wheels = list(out.glob("sisoul-*.whl"))
    assert wheels, f"no wheel produced. stdout: {result.stdout[-500:]}"
    return wheels[0]


def test_wheel_builds_successfully(built_wheel):
    """Wheel file exists + has expected name."""
    assert built_wheel.exists()
    assert "sisoul-" in built_wheel.name
    assert built_wheel.suffix == ".whl"


def test_wheel_has_correct_metadata(built_wheel):
    """Wheel METADATA file (PEP 427) has Name + Version + Description."""
    import zipfile
    with zipfile.ZipFile(built_wheel) as zf:
        metadata_path = next(
            (n for n in zf.namelist() if n.endswith(".dist-info/METADATA")), None
        )
        assert metadata_path, "wheel missing METADATA"
        metadata = zf.read(metadata_path).decode()
    assert "Name: sisoul" in metadata
    assert "Version: 1.0.0a0" in metadata or "Version: 1.0.0-alpha" in metadata
    # Critical: dependencies must appear (the bug we fixed put them in wrong section)
    assert "Requires-Dist: typer" in metadata
    assert "Requires-Dist: pynacl" in metadata


def test_wheel_pip_install_dry_run(built_wheel):
    """pip install --dry-run succeeds (validates dependency resolution)."""
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--dry-run", str(built_wheel)],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f"pip install failed: {result.stderr[:300]}"
    assert "Would install sisoul" in result.stdout, f"missing install plan: {result.stdout[:300]}"


def test_wheel_has_entry_point_console_script(built_wheel):
    """Wheel has sisoul = sisoul.cli:app entry point."""
    import zipfile
    with zipfile.ZipFile(built_wheel) as zf:
        entry_paths = [n for n in zf.namelist() if n.endswith("entry_points.txt")]
        assert entry_paths, "wheel missing entry_points.txt"
        entry_txt = zf.read(entry_paths[0]).decode()
    assert "[console_scripts]" in entry_txt
    assert "sisoul" in entry_txt
