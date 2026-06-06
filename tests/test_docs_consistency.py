"""Test documentation consistency: no broken links, version match, required files exist."""
from __future__ import annotations
import re
from pathlib import Path

import pytest


REPO = Path(__file__).parent.parent


def test_required_root_docs_exist():
    """All root-level docs must exist for alpha launch."""
    required = [
        "README.md", "LICENSE", "NOTICE",
        "CHANGELOG.md", "SECURITY.md", "CONTRIBUTING.md", "THANKS.md",
        "RELEASE-NOTES-v1.0-alpha.md", "ALPHA-LAUNCH-CHECKLIST.md",
        "pyproject.toml", "VERSION", "Makefile",
        ".gitignore", ".editorconfig", ".gitattributes", ".markdownlint.yaml",
        ".pre-commit-config.yaml", "tox.ini",
        "Dockerfile", "docker-compose.yml", ".dockerignore",
        "mkdocs.yml",
    ]
    missing = [f for f in required if not (REPO / f).exists()]
    assert not missing, f"missing required docs: {missing}"


def test_required_docs_subdir_exist():
    """docs/ files required for alpha launch."""
    required = [
        "docs/QUICK-START.md",
        "docs/ARCHITECTURE.md",
        "docs/FAQ.md",
        "docs/CI-CD-WORKFLOWS.md",
        "docs/ALPHA-LAUNCH-PLAYBOOK.md",
        "docs/ALPHA-LAUNCH-ANNOUNCEMENT-DRAFTS.md",
        "docs/index.md",
        "docs/i18n/zh-CN/QUICK-START.md",
    ]
    missing = [f for f in required if not (REPO / f).exists()]
    assert not missing, f"missing docs: {missing}"


def test_examples_exist():
    required = [
        "examples/README.md",
        "examples/python_client_basic.py",
        "examples/python_client_debate.py",
        "examples/python_client_pipeline.py",
        "examples/bash_smoke_test.sh",
        "examples/monitoring_grafana_queries.txt",
    ]
    missing = [f for f in required if not (REPO / f).exists()]
    assert not missing, f"missing examples: {missing}"


def test_ops_artifacts_exist():
    required = [
        "ops/install.sh",
        "ops/install-dev.sh",
        "ops/init/sisoul-daemon.service",
        "ops/init/com.sisoul.daemon.plist",
        "ops/init/install-autostart.sh",
        "ops/init/README.md",
        "ops/prometheus.yml",
        "ops/prometheus-alerts.yml",
        "ops/grafana-dashboard.json",
    ]
    missing = [f for f in required if not (REPO / f).exists()]
    assert not missing, f"missing ops: {missing}"


def test_github_templates_exist():
    required = [
        ".github/workflows/ci.yml",
        ".github/workflows/release.yml",
        "pwa/.github/workflows/deploy-gh-pages.yml",
        ".github/ISSUE_TEMPLATE/bug_report.md",
        ".github/ISSUE_TEMPLATE/feature_request.md",
        ".github/PULL_REQUEST_TEMPLATE.md",
    ]
    missing = [f for f in required if not (REPO / f).exists()]
    assert not missing, f"missing github files: {missing}"


def test_version_consistency():
    """VERSION file matches pyproject.toml matches __init__.py."""
    version_file = (REPO / "VERSION").read_text().strip()

    pyproject = (REPO / "pyproject.toml").read_text()
    pyproject_match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M)
    assert pyproject_match, "pyproject.toml missing version"
    assert version_file == pyproject_match.group(1), \
        f"VERSION ({version_file}) != pyproject.toml ({pyproject_match.group(1)})"

    init = (REPO / "src" / "sisoul" / "__init__.py").read_text()
    init_match = re.search(r'__version__\s*=\s*"([^"]+)"', init)
    assert init_match, "__init__.py missing __version__"
    assert version_file == init_match.group(1), \
        f"VERSION ({version_file}) != __init__.py ({init_match.group(1)})"


def test_license_apache_2():
    """LICENSE is Apache-2.0 official text."""
    license_text = (REPO / "LICENSE").read_text()
    assert "Apache License" in license_text
    assert "Version 2.0" in license_text
    assert "http://www.apache.org/licenses/" in license_text


def test_readme_has_install_command():
    """README has a working install path: alpha source install OR post-release curl one-liner."""
    readme = (REPO / "README.md").read_text()
    has_curl = "curl -sSfL" in readme and "install.sh" in readme
    has_source = "git clone" in readme and "pip install -e" in readme
    assert has_curl or has_source, (
        "README must show either curl install.sh or git-clone source-install"
    )


def test_changelog_references_current_version():
    """CHANGELOG.md mentions current 1.0.0-alpha."""
    changelog = (REPO / "CHANGELOG.md").read_text()
    assert "1.0.0-alpha" in changelog


def test_security_has_disclosure_channel():
    """SECURITY.md documents how to report a vulnerability."""
    security = (REPO / "SECURITY.md").read_text()
    assert "security@" in security or "GitHub" in security
    assert "vulnerab" in security.lower() or "disclos" in security.lower()


def test_pyproject_has_classifiers():
    """pyproject.toml has PyPI classifiers (Development Status, License, ...)."""
    pyproject = (REPO / "pyproject.toml").read_text()
    assert "classifiers" in pyproject
    assert "Development Status :: 3 - Alpha" in pyproject
    assert "License :: OSI Approved :: Apache Software License" in pyproject


def test_pyproject_has_urls():
    """pyproject.toml has project.urls section."""
    pyproject = (REPO / "pyproject.toml").read_text()
    assert "[project.urls]" in pyproject
    assert "Homepage" in pyproject
    assert "Repository" in pyproject


def test_dockerfile_has_healthcheck():
    """Dockerfile includes HEALTHCHECK directive."""
    dockerfile = (REPO / "Dockerfile").read_text()
    assert "HEALTHCHECK" in dockerfile
    assert "/sisoul/health" in dockerfile


def test_obs_sprint_docs_referenced():
    """Sprint obs documents (in vault) exist."""
    obs_root = Path.home() / ".sisoul-internal-notes"
    if not obs_root.exists():
        pytest.skip("obs vault not present (CI environment)")
    for n in [63, 64, 65, 66, 67, 68, 69]:
        matches = list(obs_root.glob(f"{n}-*.md"))
        assert matches, f"obs §{n} missing"
