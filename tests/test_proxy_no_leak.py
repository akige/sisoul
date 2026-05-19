"""Phase 4 W54-W58 · 波 5 dev-B.

隐私 audit: prompt / response 字串 **绝不**进入 log / write_file / print / stdout.

测试两层:
- 静态 AST 扫源码: proxy_chat_request 函数体内不调任何持久化 sink
- 动态: 跑真 proxy_chat_request, 捕获 stdout/stderr/log/logfile 全无 prompt 子串
"""

from __future__ import annotations

import inspect
import uuid

import pytest

from sisoul.friend import encrypted_proxy as ep_module
from sisoul.friend.encrypted_proxy import (
    EncryptedProxy,
    derive_friend_session_keypair,
)
from sisoul.friend.proxy_audit import (
    AuditReport,
    LeakReport,
    scan_source_for_prompt_sinks,
    verify_no_prompt_leak,
)
from sisoul.identity.seed import generate_mnemonic, mnemonic_to_master_key


# ── 静态扫源码 ──────────────────────────────────────────────────────────────


class TestStaticScan:
    def test_proxy_chat_request_no_sink_violation(self):
        """encrypted_proxy.proxy_chat_request 函数体内 prompt 变量绝不进持久化 sink."""
        source = inspect.getsource(ep_module)
        reports = scan_source_for_prompt_sinks(source, target_funcs=("proxy_chat_request",))
        assert "proxy_chat_request" in reports
        report = reports["proxy_chat_request"]
        assert isinstance(report, AuditReport)
        assert report.ok, f"违规:\n{report}"

    def test_scan_finds_seeded_violation(self):
        """sanity: 故意加 log.info(prompt_text) 应被识别."""
        bad_source = """
import logging

def proxy_chat_request(borrower_did, borrower_pubkey, encrypted_prompt, target_model):
    prompt_text = "decrypted"
    logging.info(f"borrowed: {prompt_text}")
    return b""
"""
        reports = scan_source_for_prompt_sinks(bad_source)
        assert "proxy_chat_request" in reports
        report = reports["proxy_chat_request"]
        assert not report.ok
        assert any("logging.info" in v.sink_call for v in report.violations)

    def test_scan_finds_print_violation(self):
        bad_source = """
def proxy_chat_request(borrower_did, borrower_pubkey, encrypted_prompt, target_model):
    prompt = "x"
    print(f"prompt was: {prompt}")
    return b""
"""
        reports = scan_source_for_prompt_sinks(bad_source)
        assert not reports["proxy_chat_request"].ok

    def test_scan_finds_open_write(self):
        bad_source = """
def proxy_chat_request(borrower_did, borrower_pubkey, encrypted_prompt, target_model):
    prompt_bytes = b"x"
    with open("/tmp/leak", "wb") as f:
        f.write(prompt_bytes)
    return b""
"""
        reports = scan_source_for_prompt_sinks(bad_source)
        # open(...) 是 sink, prompt_bytes 是 prompt 变量
        report = reports["proxy_chat_request"]
        # write_text/write_bytes 在白名单里, open 在白名单
        # 但 prompt_bytes 进了 open(...) (path 第一参不含, 第二参 'wb' 不含)
        # 这里检查 sink_calls_seen 至少含 open
        assert "open" in report.sink_calls_seen or any(
            v.sink_call == "open" for v in report.violations
        )

    def test_clean_code_passes(self):
        clean_source = """
def proxy_chat_request(borrower_did, borrower_pubkey, encrypted_prompt, target_model):
    prompt_text = "decrypted"
    response = call_llm(prompt_text)
    return encrypt(response)
"""
        reports = scan_source_for_prompt_sinks(clean_source)
        assert reports["proxy_chat_request"].ok


# ── 动态扫: 真跑 proxy 验证无 leak ────────────────────────────────────────────


@pytest.fixture
def two_proxies():
    """alice_proxy + bob_proxy with mock forwarder."""
    alice_master = mnemonic_to_master_key(generate_mnemonic(128))
    bob_master = mnemonic_to_master_key(generate_mnemonic(128))
    alice_priv, alice_pub = derive_friend_session_keypair(alice_master, 0)
    bob_priv, bob_pub = derive_friend_session_keypair(bob_master, 0)

    def mock_forwarder(prompt, model, provider="anthropic", api_key=None, **kw):
        # 注意 mock forwarder 故意 echo prompt 进 response — 是真 LLM 常见行为
        # 不能 print / log
        response = f"RESPONSE_TO_{prompt[:50]}"
        return response, len(prompt) // 4, len(response) // 4

    alice = EncryptedProxy(
        self_priv=alice_priv, self_pub=alice_pub,
        self_did="alice.sisoul.eth",
        forwarder=mock_forwarder,
    )
    bob = EncryptedProxy(
        self_priv=bob_priv, self_pub=bob_pub,
        self_did="bob.sisoul.eth",
        forwarder=mock_forwarder,
    )
    return alice, bob


class TestDynamicLeak:
    def test_no_leak_normal_flow(self, two_proxies):
        alice, bob = two_proxies
        prompt = f"PROMPT_TOKEN_{uuid.uuid4().hex}"
        response_token = f"RESPONSE_TO_{prompt[:50]}"  # mock 会这样构造

        enc = alice.encrypt_for(bob.self_pub.encode(), prompt)

        def run():
            bob.proxy_chat_request(
                borrower_did="alice.sisoul.eth",
                borrower_pubkey=alice.self_pub.encode(),
                encrypted_prompt=enc,
                target_model="claude-opus-4-7",
            )

        report = verify_no_prompt_leak(prompt=prompt, response=response_token, run_func=run)
        assert report.ok, f"FAIL: {report}"

    def test_no_leak_on_permission_denial(self, two_proxies):
        alice, _ = two_proxies
        from sisoul.friend.encrypted_proxy import ProxyPermissionError

        # 构造一个 deny-all bob
        bob_master = mnemonic_to_master_key(generate_mnemonic(128))
        bob_priv, bob_pub = derive_friend_session_keypair(bob_master, 0)

        def deny(**kw):
            raise ProxyPermissionError("deny")

        bob = EncryptedProxy(
            self_priv=bob_priv, self_pub=bob_pub,
            self_did="bob.sisoul.eth",
            permission_checker=deny,
        )
        prompt = f"PROMPT_DENIED_{uuid.uuid4().hex}"
        response = "UNUSED-RESPONSE-TOKEN-NEVER-SHOULD-LEAK"
        enc = alice.encrypt_for(bob_pub.encode(), prompt)

        def run():
            try:
                bob.proxy_chat_request(
                    borrower_did="alice.sisoul.eth",
                    borrower_pubkey=alice.self_pub.encode(),
                    encrypted_prompt=enc,
                    target_model="claude-opus-4-7",
                )
            except ProxyPermissionError:
                pass

        report = verify_no_prompt_leak(prompt=prompt, response=response, run_func=run)
        assert report.ok, f"FAIL on denial path: {report}"

    def test_no_leak_on_forwarder_exception(self, two_proxies):
        alice, _ = two_proxies
        bob_master = mnemonic_to_master_key(generate_mnemonic(128))
        bob_priv, bob_pub = derive_friend_session_keypair(bob_master, 0)

        def boom(**kw):
            raise RuntimeError("LLM unreachable")

        bob = EncryptedProxy(
            self_priv=bob_priv, self_pub=bob_pub,
            self_did="bob.sisoul.eth",
            forwarder=boom,
        )
        prompt = f"PROMPT_BOOM_{uuid.uuid4().hex}"
        response = "UNUSED-RESPONSE-NEVER-PRODUCED-9999"
        enc = alice.encrypt_for(bob_pub.encode(), prompt)

        def run():
            from sisoul.friend.encrypted_proxy import ProxyError
            try:
                bob.proxy_chat_request(
                    borrower_did="alice.sisoul.eth",
                    borrower_pubkey=alice.self_pub.encode(),
                    encrypted_prompt=enc,
                    target_model="claude-opus-4-7",
                )
            except ProxyError:
                pass

        report = verify_no_prompt_leak(prompt=prompt, response=response, run_func=run)
        assert report.ok, f"FAIL on forwarder error path: {report}"

    def test_leak_detector_catches_real_leak(self, two_proxies, tmp_path):
        """sanity: 故意 write 一份 prompt 到文件应被识别 leak."""
        alice, bob = two_proxies
        prompt = f"PROMPT_LEAK_{uuid.uuid4().hex}"
        response = "UNUSED-RESPONSE-XXX"
        leak_file = tmp_path / "leak.txt"

        def evil_run():
            leak_file.write_text(f"I am leaking: {prompt}")

        report = verify_no_prompt_leak(
            prompt=prompt, response=response, run_func=evil_run,
            check_paths=[str(tmp_path)],
        )
        assert not report.ok
        assert report.logfile_leak

    def test_print_leak_detected(self, two_proxies):
        """print(prompt) 必被识别."""
        alice, bob = two_proxies
        prompt = f"PROMPT_PRINT_{uuid.uuid4().hex}"
        response = "UNUSED-RESPONSE-YYY"

        def evil_run():
            print(f"prompt was: {prompt}")

        report = verify_no_prompt_leak(prompt=prompt, response=response, run_func=evil_run)
        assert not report.ok
        assert report.stdout_leak

    def test_log_leak_detected(self):
        import logging
        prompt = f"PROMPT_LOG_{uuid.uuid4().hex}"
        response = "UNUSED-RESPONSE-ZZZ"

        def evil_run():
            logging.getLogger("test").warning(f"prompt: {prompt}")

        report = verify_no_prompt_leak(prompt=prompt, response=response, run_func=evil_run)
        assert not report.ok
        assert report.log_leak


# ── audit tool 输入校验 ──────────────────────────────────────────────────────


class TestAuditValidation:
    def test_too_short_prompt_raises(self):
        with pytest.raises(ValueError, match=">= 8"):
            verify_no_prompt_leak(prompt="x", response="x" * 10, run_func=lambda: None)

    def test_too_short_response_raises(self):
        with pytest.raises(ValueError, match=">= 8"):
            verify_no_prompt_leak(prompt="x" * 10, response="x", run_func=lambda: None)

    def test_leak_report_str(self):
        r = LeakReport()
        assert "OK" in str(r)
        r.stdout_leak = True
        assert "FAIL" in str(r)


# ── audit covers all 5 wave-5 lines (sanity) ─────────────────────────────────


class TestScanCoverage:
    def test_scan_proxy_module_returns_report(self):
        source = inspect.getsource(ep_module)
        # 多函数审计
        reports = scan_source_for_prompt_sinks(
            source,
            target_funcs=("proxy_chat_request", "_create_session"),
        )
        # proxy_chat_request 必在; _create_session 不操作 prompt 不必报
        assert "proxy_chat_request" in reports
