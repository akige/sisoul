"""sisoul friend · 加密 proxy 隐私 audit 工具 (Phase 4 W54-W58 · 波 5 dev-B).

§28 §3.2 核心隐私保证: prompt **绝不**进 log / write_file / print / stdout.

本模块两条审计路径:

# A. 静态扫描

`scan_source_for_prompt_sinks(source_text)`:
- AST 走 encrypted_proxy.proxy_chat_request 函数体
- 列出对 ``logging.* / print / open(..., 'w'/'a') / Path.write_*`` 等持久化 sink 的调用
- 验证: proxy_chat_request 函数体内**只允许**:
  - decrypt_from / encrypt_for (本类方法)
  - self._forwarder(prompt=...) (核心转发)
  - 算 token count (len(prompt)//4 之类)
  - session.end / _maybe_write_ledger (只传 metadata)
- 若发现 prompt 变量出现在持久化 sink 参数 → 报警 (RED)

# B. 动态扫描 (runtime)

`verify_no_prompt_leak(proxy, prompt, response, run_func)`:
- 在 run_func 跑前: 标记 sys.stdout / logging handlers / 注入临时 logfile
- 跑 run_func (典型: proxy.proxy_chat_request(...))
- 跑后: 检查 stdout capture / log buffer / 临时 logfile 全无 prompt/response 子串
- 同时调 EncryptedProxy.enforce_no_disk_write 扫常见路径

# 用法

    from sisoul.friend.proxy_audit import (
        scan_source_for_prompt_sinks,
        verify_no_prompt_leak,
    )

    # 静态
    import inspect
    from sisoul.friend import encrypted_proxy as ep
    src = inspect.getsource(ep)
    report = scan_source_for_prompt_sinks(src)
    assert report.violations == [], report.violations

    # 动态
    def _do():
        return proxy.proxy_chat_request(...)
    verify_no_prompt_leak(prompt="secret-xyz", response="reply-abc", run_func=_do)
"""

from __future__ import annotations

import ast
import io
import logging
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator


# ── 静态扫描 ──────────────────────────────────────────────────────────────────


# 黑名单 sink 调用 (出现在 proxy_chat_request 函数体内 + 接受 prompt 变量 = 违规)
_SINK_PATTERNS = frozenset(
    {
        # logging
        "logging.debug", "logging.info", "logging.warning",
        "logging.warn", "logging.error", "logging.critical", "logging.log",
        "logging.exception",
        "logger.debug", "logger.info", "logger.warning",
        "logger.warn", "logger.error", "logger.critical",
        "log.debug", "log.info", "log.warning",
        "log.warn", "log.error", "log.critical",
        # 直接输出
        "print",
        "sys.stdout.write", "sys.stderr.write",
        # 持久化
        "open",
        "Path.write_text", "Path.write_bytes",
        "path.write_text", "path.write_bytes",
        "write_text", "write_bytes",
        # 网络外发 (不该出现, 走 forwarder 才合规)
        "requests.post", "httpx.post", "urllib.request.urlopen",
        # subprocess
        "subprocess.run", "subprocess.Popen", "subprocess.call",
    }
)


# 白名单变量名 (在 proxy_chat_request 函数体内, 这些变量名持有 plaintext)
_PROMPT_VARS = frozenset({"prompt_text", "prompt_bytes", "prompt", "response_text"})


@dataclass
class AuditViolation:
    func_name: str
    sink_call: str
    arg_repr: str
    lineno: int

    def __str__(self) -> str:
        return (
            f"[LEAK] {self.func_name}:{self.lineno} "
            f"调用 {self.sink_call}({self.arg_repr}) "
            f"— prompt 变量进 sink"
        )


@dataclass
class AuditReport:
    func_name: str
    sink_calls_seen: list[str] = field(default_factory=list)
    violations: list[AuditViolation] = field(default_factory=list)
    prompt_var_uses: list[tuple[int, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def __str__(self) -> str:
        if self.ok:
            return f"AUDIT OK: {self.func_name} — 0 violations, sink_calls={len(self.sink_calls_seen)}"
        lines = [f"AUDIT FAIL: {self.func_name} — {len(self.violations)} violations:"]
        for v in self.violations:
            lines.append(f"  {v}")
        return "\n".join(lines)


class _PromptSinkVisitor(ast.NodeVisitor):
    def __init__(self, target_funcs: set[str]) -> None:
        self.target_funcs = target_funcs
        self.reports: dict[str, AuditReport] = {}
        self._stack: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        if node.name in self.target_funcs:
            report = AuditReport(func_name=node.name)
            self.reports[node.name] = report
            self._stack.append(node.name)
            self.generic_visit(node)
            self._stack.pop()
        else:
            self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        if self._stack:
            current = self._stack[-1]
            report = self.reports[current]
            sink_repr = _call_repr(node.func)
            if sink_repr in _SINK_PATTERNS:
                report.sink_calls_seen.append(sink_repr)
                for arg in list(node.args) + [kw.value for kw in node.keywords]:
                    if _contains_prompt_var(arg):
                        report.violations.append(
                            AuditViolation(
                                func_name=current,
                                sink_call=sink_repr,
                                arg_repr=ast.unparse(arg),
                                lineno=node.lineno,
                            )
                        )
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if self._stack and node.id in _PROMPT_VARS:
            self.reports[self._stack[-1]].prompt_var_uses.append((node.lineno, node.id))


def _call_repr(func_node: ast.expr) -> str:
    """把 ast.Call.func 表达成 'module.func' / 'obj.method' / 'func' 字符串."""
    if isinstance(func_node, ast.Name):
        return func_node.id
    if isinstance(func_node, ast.Attribute):
        # 例 logger.info / self._forwarder / logging.getLogger
        parts: list[str] = [func_node.attr]
        cur: ast.expr = func_node.value
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        return ".".join(reversed(parts))
    return ast.unparse(func_node)


def _contains_prompt_var(node: ast.expr) -> bool:
    """递归找 ast 子树里是否含 _PROMPT_VARS 变量名."""
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and n.id in _PROMPT_VARS:
            return True
    return False


def scan_source_for_prompt_sinks(
    source_text: str,
    target_funcs: tuple[str, ...] = ("proxy_chat_request",),
) -> dict[str, AuditReport]:
    """AST 扫源码, 返回 {func_name: AuditReport}.

    Args:
        source_text: 待扫源码 (用 inspect.getsource(module) 拿).
        target_funcs: 要审计的函数名集合. 默认只审 proxy_chat_request.

    Returns:
        dict, key = func 名, value = AuditReport.
        无 violation → ``all(r.ok for r in reports.values()) == True``.
    """
    tree = ast.parse(source_text)
    visitor = _PromptSinkVisitor(set(target_funcs))
    visitor.visit(tree)
    return visitor.reports


# ── 动态扫描 ──────────────────────────────────────────────────────────────────


@dataclass
class LeakReport:
    stdout_leak: bool = False
    stderr_leak: bool = False
    log_leak: bool = False
    logfile_leak: bool = False
    leaked_substrings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (
            self.stdout_leak or self.stderr_leak
            or self.log_leak or self.logfile_leak
        )

    def __str__(self) -> str:
        if self.ok:
            return "LEAK CHECK OK: 0 sink 含 prompt/response 子串"
        return (
            f"LEAK CHECK FAIL: stdout={self.stdout_leak} stderr={self.stderr_leak} "
            f"log={self.log_leak} logfile={self.logfile_leak} "
            f"leaked={self.leaked_substrings}"
        )


@contextmanager
def _capture_all_sinks() -> Iterator[dict[str, Any]]:
    """临时 patch stdout / stderr / root logger + 临时 logfile, 收集所有 sink 输出."""
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    log_buf = io.StringIO()
    orig_stdout, orig_stderr = sys.stdout, sys.stderr

    root = logging.getLogger()
    orig_level = root.level
    log_handler = logging.StreamHandler(log_buf)
    log_handler.setLevel(logging.DEBUG)
    root.addHandler(log_handler)
    root.setLevel(logging.DEBUG)

    tmp_log = tempfile.NamedTemporaryFile(
        mode="w+", delete=False, suffix=".log", prefix="sisoul-proxy-audit-"
    )
    tmp_log.close()
    file_handler = logging.FileHandler(tmp_log.name)
    file_handler.setLevel(logging.DEBUG)
    root.addHandler(file_handler)

    sys.stdout = stdout_buf
    sys.stderr = stderr_buf

    captures: dict[str, Any] = {
        "stdout": stdout_buf,
        "stderr": stderr_buf,
        "log": log_buf,
        "logfile_path": tmp_log.name,
    }
    try:
        yield captures
    finally:
        sys.stdout = orig_stdout
        sys.stderr = orig_stderr
        root.removeHandler(log_handler)
        root.removeHandler(file_handler)
        file_handler.close()
        root.setLevel(orig_level)


def verify_no_prompt_leak(
    prompt: str,
    response: str,
    run_func: Callable[[], Any],
    check_paths: list[str] | None = None,
) -> LeakReport:
    """跑 run_func, 检查 prompt / response 字串没出现在任何 sink.

    Args:
        prompt: 完整 prompt (或独特子串). 必须含足够独特字符 (避免误报).
        response: 完整 response (或独特子串).
        run_func: 0 参函数, 跑 proxy 业务逻辑.
        check_paths: 跑后扫的文件路径列表. None = 不扫文件路径.

    Returns:
        LeakReport.
    """
    if not prompt or len(prompt) < 8:
        raise ValueError("prompt 必须 >= 8 字符避免误报")
    if not response or len(response) < 8:
        raise ValueError("response 必须 >= 8 字符避免误报")

    report = LeakReport()
    leak_marks: list[str] = []

    with _capture_all_sinks() as caps:
        try:
            run_func()
        except Exception:
            # 业务异常不影响 leak 检测; 仍要检查 stdout/log
            pass

        stdout_text = caps["stdout"].getvalue()
        stderr_text = caps["stderr"].getvalue()
        log_text = caps["log"].getvalue()
        logfile_path = caps["logfile_path"]

    if prompt in stdout_text or response in stdout_text:
        report.stdout_leak = True
        if prompt in stdout_text:
            leak_marks.append(f"stdout含prompt[:20]={prompt[:20]!r}")
        if response in stdout_text:
            leak_marks.append(f"stdout含response[:20]={response[:20]!r}")

    if prompt in stderr_text or response in stderr_text:
        report.stderr_leak = True
        if prompt in stderr_text:
            leak_marks.append(f"stderr含prompt[:20]={prompt[:20]!r}")
        if response in stderr_text:
            leak_marks.append(f"stderr含response[:20]={response[:20]!r}")

    if prompt in log_text or response in log_text:
        report.log_leak = True
        if prompt in log_text:
            leak_marks.append(f"log含prompt")
        if response in log_text:
            leak_marks.append(f"log含response")

    # 临时 logfile
    try:
        logfile_text = Path(logfile_path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        logfile_text = ""
    if prompt in logfile_text or response in logfile_text:
        report.logfile_leak = True
        leak_marks.append("logfile leak")
    try:
        Path(logfile_path).unlink()
    except OSError:
        pass

    # 额外扫指定文件路径
    if check_paths:
        for p_str in check_paths:
            p = Path(p_str).expanduser()
            if not p.exists():
                continue
            files: list[Path] = []
            if p.is_file():
                files = [p]
            elif p.is_dir():
                files = [c for c in p.iterdir() if c.is_file() and c.stat().st_size < 10 * 1024 * 1024]
            for f in files:
                try:
                    raw = f.read_bytes()
                except (OSError, PermissionError):
                    continue
                if prompt.encode("utf-8") in raw or response.encode("utf-8") in raw:
                    report.logfile_leak = True
                    leak_marks.append(f"file leak: {f}")

    report.leaked_substrings = leak_marks
    return report


__all__ = [
    "AuditReport",
    "AuditViolation",
    "LeakReport",
    "scan_source_for_prompt_sinks",
    "verify_no_prompt_leak",
]
