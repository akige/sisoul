"""Security: no hardcoded secrets in source code.

Critical for open-source publish. Run via pre-commit or CI.
"""
from __future__ import annotations
import re
from pathlib import Path

import pytest


REPO = Path(__file__).parent.parent


# Patterns that indicate hardcoded secrets (would leak when repo goes public).
SECRET_PATTERNS = [
    # Anthropic
    (r"sk-ant-[a-zA-Z0-9_-]{20,}", "Anthropic API key"),
    # OpenAI
    (r"sk-proj-[a-zA-Z0-9_-]{20,}", "OpenAI project key"),
    (r"sk-[a-zA-Z0-9]{40,}", "Generic sk-* key (≥40 chars)"),
    # GitHub
    (r"ghp_[a-zA-Z0-9]{36}", "GitHub Personal Access Token"),
    (r"github_pat_[a-zA-Z0-9_]{50,}", "GitHub Fine-grained PAT"),
    # AWS
    (r"AKIA[0-9A-Z]{16}", "AWS Access Key ID"),
    # Tailscale
    (r"tskey-[a-zA-Z0-9-]{30,}", "Tailscale API key"),
    # JWT-ish
    (r"eyJ[A-Za-z0-9_-]{30,}\.[A-Za-z0-9_-]{30,}\.[A-Za-z0-9_-]+", "JWT token (suspicious in src)"),
    # Private keys
    (r"-----BEGIN (RSA |EC )?PRIVATE KEY-----", "Private key block"),
    (r"-----BEGIN OPENSSH PRIVATE KEY-----", "OpenSSH private key"),
]


# Files / dirs to skip (false positives expected)
SKIP_PATTERNS = [
    ".venv/", ".git/", "node_modules/", "dist/", "build/",
    ".pytest_cache/", "__pycache__/", ".coverage", "uv.lock",
    "tests/test_security_no_secrets.py",  # self (contains the patterns above)
    "docs/whitepaper/",  # may have public example keys
    "src/sisoul.egg-info/",  # generated, contains absolute paths
    ".mac-temp/", "qa/", ".ruff_cache/", ".mypy_cache/",
    "/uv.lock", "package-lock.json", "yarn.lock",  # lockfiles
]


def _should_skip(path: Path) -> bool:
    """Skip files matching SKIP_PATTERNS or non-text."""
    rel = str(path.relative_to(REPO))
    for skip in SKIP_PATTERNS:
        if skip in rel:
            return True
    # binary by extension
    if path.suffix in (".png", ".jpg", ".jpeg", ".gif", ".pdf", ".whl", ".tar.gz", ".so", ".dylib", ".safetensors", ".parquet", ".zip"):
        return True
    return False


def test_no_anthropic_keys_in_source():
    """No `sk-ant-...` keys in tracked source."""
    pattern, name = SECRET_PATTERNS[0]
    rex = re.compile(pattern)
    matches = []
    for path in REPO.rglob("*"):
        if not path.is_file() or _should_skip(path):
            continue
        try:
            text = path.read_text(errors="ignore")
        except Exception:
            continue
        for line_num, line in enumerate(text.splitlines(), 1):
            if rex.search(line):
                matches.append((path.relative_to(REPO), line_num, line[:100]))
    assert not matches, f"{name} leaked in source: {matches[:3]}"


def test_no_openai_keys_in_source():
    pattern, name = SECRET_PATTERNS[1]
    rex = re.compile(pattern)
    matches = []
    for path in REPO.rglob("*"):
        if not path.is_file() or _should_skip(path):
            continue
        try:
            text = path.read_text(errors="ignore")
        except Exception:
            continue
        for line_num, line in enumerate(text.splitlines(), 1):
            if rex.search(line):
                matches.append((path.relative_to(REPO), line_num, line[:100]))
    assert not matches, f"{name} leaked in source: {matches[:3]}"


def test_no_github_tokens_in_source():
    for pattern, name in SECRET_PATTERNS[3:5]:
        rex = re.compile(pattern)
        matches = []
        for path in REPO.rglob("*"):
            if not path.is_file() or _should_skip(path):
                continue
            try:
                text = path.read_text(errors="ignore")
            except Exception:
                continue
            for line_num, line in enumerate(text.splitlines(), 1):
                if rex.search(line):
                    matches.append((path.relative_to(REPO), line_num, line[:80]))
        assert not matches, f"{name} leaked in source: {matches[:3]}"


def test_no_aws_keys_in_source():
    pattern, name = SECRET_PATTERNS[5]
    rex = re.compile(pattern)
    matches = []
    for path in REPO.rglob("*"):
        if not path.is_file() or _should_skip(path):
            continue
        try:
            text = path.read_text(errors="ignore")
        except Exception:
            continue
        for line_num, line in enumerate(text.splitlines(), 1):
            if rex.search(line):
                matches.append((path.relative_to(REPO), line_num, line[:80]))
    assert not matches, f"{name} leaked in source: {matches[:3]}"


def test_no_private_keys_in_source():
    for pattern, name in SECRET_PATTERNS[8:10]:
        rex = re.compile(pattern)
        matches = []
        for path in REPO.rglob("*"):
            if not path.is_file() or _should_skip(path):
                continue
            try:
                text = path.read_text(errors="ignore")
            except Exception:
                continue
            if rex.search(text):
                matches.append(path.relative_to(REPO))
        assert not matches, f"{name} leaked: {matches[:3]}"


def test_no_password_assignments():
    """Catches lines like `password = "actual"`."""
    rex = re.compile(r'(password|passwd|api_key|secret)\s*=\s*["\'][a-zA-Z0-9+/=_-]{16,}["\']', re.I)
    matches = []
    for path in REPO.rglob("*.py"):
        if _should_skip(path):
            continue
        try:
            text = path.read_text(errors="ignore")
        except Exception:
            continue
        for line_num, line in enumerate(text.splitlines(), 1):
            m = rex.search(line)
            if m:
                # Allowlist common test fixtures (mock values, not real creds)
                allowed = [
                    "redacted", "TEST_", "PLACEHOLDER", "your-",
                    "example", "stub", "mock", "<", "{",
                    "ULTRA_SECRET", "PROMPTGUARD", "SECRET_TOKEN",
                    "fake", "FAKE", "FOUNDATION_STUB",
                ]
                if any(skip in line for skip in allowed):
                    continue
                # Also skip test files explicitly (test fixtures, not prod code)
                if str(path.relative_to(REPO)).startswith("tests/"):
                    continue
                matches.append((path.relative_to(REPO), line_num, m.group(0)[:60]))
    assert not matches, f"hardcoded credentials: {matches[:3]}"
