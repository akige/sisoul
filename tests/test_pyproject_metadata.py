"""Test pyproject.toml metadata completeness for PyPI publish."""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

REPO = Path(__file__).parent.parent
PYPROJECT = REPO / "pyproject.toml"


@pytest.fixture
def pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text())


def test_has_project_section(pyproject):
    assert "project" in pyproject


def test_has_name(pyproject):
    assert pyproject["project"]["name"] == "sisoul"


def test_has_version(pyproject):
    assert pyproject["project"]["version"] == "1.0.0-alpha"


def test_has_description(pyproject):
    desc = pyproject["project"]["description"]
    assert len(desc) > 30
    assert "P2P" in desc or "decentralized" in desc.lower() or "ai" in desc.lower()


def test_requires_python(pyproject):
    assert pyproject["project"]["requires-python"] == ">=3.11"


def test_has_license(pyproject):
    assert pyproject["project"]["license"] == "AGPL-3.0-or-later"


def test_has_readme(pyproject):
    assert pyproject["project"]["readme"] == "README.md"


def test_has_keywords(pyproject):
    kw = pyproject["project"]["keywords"]
    assert len(kw) >= 10
    assert "p2p" in kw
    assert "decentralized" in kw


def test_has_classifiers(pyproject):
    classifiers = pyproject["project"]["classifiers"]
    assert len(classifiers) >= 10
    joined = " ".join(classifiers)
    assert "Development Status :: 3 - Alpha" in joined
    assert "License :: OSI Approved :: GNU Affero General Public License v3 or later (AGPLv3+)" in joined
    assert "Python :: 3.11" in joined
    assert "Python :: 3.12" in joined


def test_has_urls(pyproject):
    urls = pyproject["project"]["urls"]
    required = ["Homepage", "Documentation", "Repository", "Issues", "Changelog"]
    for u in required:
        assert u in urls, f"missing URL: {u}"


def test_dependencies_have_versions(pyproject):
    deps = pyproject["project"].get("dependencies", [])
    # all deps must have version spec (>=, ==, ~=, etc.)
    no_version = [d for d in deps if all(op not in d for op in ["==", ">=", "<=", "~=", "!="])]
    assert not no_version, f"unpinned deps: {no_version}"
    assert len(deps) >= 5, f"expected >= 5 deps, got {len(deps)}"


def test_optional_dependencies(pyproject):
    optional = pyproject["project"]["optional-dependencies"]
    # alpha launch needs these extras
    for extra in ["dev", "daemon", "llm", "chat"]:
        assert extra in optional, f"missing optional [{extra}]"


def test_build_system(pyproject):
    bs = pyproject["build-system"]
    assert "hatchling" in bs["requires"][0] or "setuptools" in bs["requires"][0]
