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


# [2026-06-12] 内部机器代号 + 内部文档路径红线 (本轮审查发现 scrub 验证 grep 没跑全:
# HANDOFF 文档曾把 tailnet/公网 IP + 机器代号推上公开 main)
INTERNAL_HOST_PATTERNS = [
    r"\baws-(us|hk|sg|jp)(-tail)?\b",
    r"\bpolaris[0-9]+(-tail)?\b",
    r"\bpanshi-sim-(prod|stage)\b",
    r"\bdmit-(jp|us)\b",
    r"\btx-jp\b",
    r"\bmihomo-tail\b",
    r"100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.[0-9]{1,3}\.[0-9]{1,3}",  # tailnet CGNAT
    r"13\.56\.68\.22",  # 曾泄漏的公网 IP
]
INTERNAL_DOC_PATHS = ["README-internal.md", "USER-WAKEUP-SUMMARY.md", "docs/internal/"]


def test_no_internal_host_names():
    import re as _re, pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    bad = []
    for f in root.rglob("*"):
        if f.is_dir() or any(s in str(f) for s in (".git/", "node_modules", ".venv", "__pycache__",
                                                    "desensitize-blacklist")):  # blacklist yaml 含 pattern 定义本身
            continue
        if f.suffix not in (".py", ".md", ".yaml", ".yml", ".toml", ".service", ".sh", ".ts", ".js", ".json"):
            continue
        try:
            text = f.read_text(errors="ignore")
        except Exception:
            continue
        # 文档示例 IP (100.64.0.x = CGNAT 段首, whitepaper 教学示例) 豁免
        text_x = _re.sub(r"100\.64\.0\.[0-9]{1,3}", "", text)
        for pat in INTERNAL_HOST_PATTERNS:
            if _re.search(pat, text_x):
                bad.append(f"{f.relative_to(root)}: {pat}")
                break
    assert not bad, "internal host/IP patterns found: " + "; ".join(bad[:10])


def test_no_internal_docs_tracked():
    import subprocess, pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True, cwd=root).stdout
    bad = [l for l in out.splitlines() if any(p in l for p in INTERNAL_DOC_PATHS)]
    assert not bad, "internal docs tracked: " + "; ".join(bad[:10])
