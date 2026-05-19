"""波 3 qa-D · 反向验证 (§J-2 第 3 条).

测 4 类错路径不 crash / 不损坏数据:
1. BIP-39 seed 缺词 / checksum 失败 → InvalidMnemonicError
2. DID handle 重名 → HandleAlreadyTakenError
3. vault encryption 用错 key → CryptoError 不 corrupt
4. PWA daemon endpoint 在 vault 不存在时不 5xx
5. cli.py restore <seed> 集成 bug (P0): 当前走 stub, 不调 dev-A run_restore_from_seed
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ─────────────────────────────────────────────────────────────────────────────
# 1. BIP-39 缺词 / checksum 失败
# ─────────────────────────────────────────────────────────────────────────────


def test_bip39_seed_missing_word_rejected():
    """11 词 mnemonic (少 1 个) 应被 verify_mnemonic 拒绝."""
    from sisoul.identity.seed import InvalidMnemonicError, mnemonic_to_master_key, verify_mnemonic

    bad = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon"
    assert verify_mnemonic(bad) is False, "11 词不合法 (缺 1 词)"
    with pytest.raises(InvalidMnemonicError):
        mnemonic_to_master_key(bad)


def test_bip39_seed_checksum_failure_rejected():
    """合法词数但 checksum 错 (最后词替换) → 拒绝."""
    from sisoul.identity.seed import InvalidMnemonicError, mnemonic_to_master_key, verify_mnemonic

    # 12 词全 abandon 不合法 (checksum 错), 标准 vector 是末词 "about"
    bad = "abandon " * 11 + "abandon"
    bad = bad.strip()
    assert verify_mnemonic(bad) is False
    with pytest.raises(InvalidMnemonicError):
        mnemonic_to_master_key(bad)


def test_bip39_save_rejects_invalid_mnemonic():
    """save_mnemonic_to_file 拒绝非法 mnemonic — 防误写垃圾覆盖 seed."""
    from sisoul.identity.seed import InvalidMnemonicError, save_mnemonic_to_file

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "seed.txt"
        with pytest.raises(InvalidMnemonicError):
            save_mnemonic_to_file("not a valid mnemonic at all", target)
        assert not target.exists(), "非法 mnemonic 写失败时文件不应被创建"


# ─────────────────────────────────────────────────────────────────────────────
# 2. DID handle 重名
# ─────────────────────────────────────────────────────────────────────────────


def test_did_register_duplicate_handle_rejected():
    """同名 handle 第二次注册 → HandleAlreadyTakenError."""
    from sisoul.identity.did import HandleAlreadyTakenError, register_did

    with tempfile.TemporaryDirectory() as tmp:
        reg = Path(tmp) / "dids.json"
        d1 = register_did("aliceqa3", registry_path=reg)
        assert d1.handle == "aliceqa3"
        with pytest.raises(HandleAlreadyTakenError):
            register_did("aliceqa3", registry_path=reg)
        # 验 registry 仍只有 1 条 (不损坏)
        data = json.loads(reg.read_text())
        assert len(data) == 1, f"重名注册失败后 registry 应保持 1 条, 实际 {len(data)}"


def test_did_register_invalid_handle_rejected():
    """handle 含非法字符 → InvalidHandleError."""
    from sisoul.identity.did import InvalidHandleError, register_did

    with tempfile.TemporaryDirectory() as tmp:
        reg = Path(tmp) / "dids.json"
        for bad in ["a@b", "a.b", "  ", "x" * 100, "-bad", "alice_qa3"]:
            with pytest.raises(InvalidHandleError):
                register_did(bad, registry_path=reg)


def test_did_mainnet_rejected_phase2():
    """Phase 2 禁止 mainnet 注册 → NetworkNotSupportedError."""
    from sisoul.identity.did import NetworkNotSupportedError, register_did

    with tempfile.TemporaryDirectory() as tmp:
        reg = Path(tmp) / "dids.json"
        with pytest.raises(NetworkNotSupportedError):
            register_did("mainnetuser", network="mainnet", registry_path=reg)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Vault encryption 用错 key
# ─────────────────────────────────────────────────────────────────────────────


def test_vault_encryption_wrong_key_raises_no_corrupt():
    """用错 master_key 解密 → CryptoError, 原密文不损坏.

    用 derive_master_key 拿 32B SecretBox key (master 64B → subkey 32B 派生).
    """
    from nacl.exceptions import CryptoError

    from sisoul.vault.encryption import decrypt_bytes, derive_master_key, encrypt_bytes

    # 用 seed-A 派 32B key_a, 用 seed-B 派 32B key_b
    seed_a = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
    seed_b = "legal winner thank year wave sausage worth useful legal winner thank yellow"
    key_a = derive_master_key(seed_a)
    key_b = derive_master_key(seed_b)
    assert len(key_a) == 32 and len(key_b) == 32
    assert key_a != key_b, "不同 seed 应派生不同 vault key"

    plain = b"secret memory: I love coffee"
    blob = encrypt_bytes(plain, key_a)
    # 错 key 解密必抛
    with pytest.raises(CryptoError):
        decrypt_bytes(blob, key_b)
    # blob 本身不被破坏, 对的 key 仍能解
    assert decrypt_bytes(blob, key_a) == plain


def test_vault_encryption_tampered_ciphertext_raises():
    """密文中间被改 1 字节 → CryptoError (MAC fail)."""
    from nacl.exceptions import CryptoError

    from sisoul.vault.encryption import decrypt_bytes, derive_master_key, encrypt_bytes

    key = derive_master_key(
        "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
    )
    plain = b"hello sisoul"
    blob = bytearray(encrypt_bytes(plain, key))
    blob[40] ^= 0xFF  # 篡改密文中部
    with pytest.raises(CryptoError):
        decrypt_bytes(bytes(blob), key)


# ─────────────────────────────────────────────────────────────────────────────
# 4. PWA daemon route 在异常 vault 不 5xx
# ─────────────────────────────────────────────────────────────────────────────


def test_pwa_route_missing_vault_no_5xx(monkeypatch):
    """vault 不存在 → API 应返 200 + 空列表 / 显式 error 字段, 不 5xx."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from sisoul.daemon_routes.pwa import router as pwa_router

    app = FastAPI()
    app.include_router(pwa_router)
    client = TestClient(app)

    with tempfile.TemporaryDirectory() as tmp:
        nonexistent = Path(tmp) / "does-not-exist"
        # 不实际创建 vault, 用 ?vault= override
        for path in ["/sisoul/preferences/list", "/sisoul/goals/list", "/sisoul/chat-history/list"]:
            r = client.get(path, params={"vault": str(nonexistent)})
            assert r.status_code < 500, f"{path} 5xx: {r.status_code} {r.text[:200]}"
            # 应为 200 空列表 或 4xx
            if r.status_code == 200:
                assert r.json() == [], f"{path} 缺 vault 应空列表, 实际 {r.json()}"


def test_pwa_route_path_traversal_blocked():
    """preferences/{id} 含 .. → 应 400 / 422 拒绝."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from sisoul.daemon_routes.pwa import router as pwa_router

    app = FastAPI()
    app.include_router(pwa_router)
    client = TestClient(app)
    # FastAPI 把 / 当 path separator, 用 url-encoded 试
    r = client.get("/sisoul/preferences/%2E%2E%2Fetc%2Fpasswd")
    assert r.status_code in (400, 404, 422), f"path traversal 未拦: {r.status_code}"


# ─────────────────────────────────────────────────────────────────────────────
# 5. CLI restore <seed> 集成 bug — 标 P0 给主集成
# ─────────────────────────────────────────────────────────────────────────────


def test_p0_pwa_router_not_mounted_on_daemon():
    """⚠️ P0: daemon.py import `pwa_router` 但 pwa.py 只 export `router`.

    后果: daemon 起来但 /sisoul/preferences/list 等 7 个 PWA endpoint 全 404.
    PWA dashboard 跑空白 (所有 fetch fail).

    daemon.py 第 84-88 行用 try/except ImportError 吞掉, 无任何日志/告警, 静默失败.

    修复 (主集成):
        # daemon.py 第 85 行 改:
        from sisoul.daemon_routes.pwa import router as pwa_router
        app.include_router(pwa_router)
        # 或者在 pwa.py 末尾加 alias: `pwa_router = router`
    """
    from sisoul.daemon import create_app
    from fastapi.testclient import TestClient

    app = create_app()
    client = TestClient(app)
    r = client.get("/sisoul/preferences/list")
    # 当前: 404 (router 未 mount); 修后: 200 (空列表 / 或 vault 数据)
    if r.status_code == 404:
        pytest.xfail(
            "P0 INTEGRATION BUG: daemon.py import 'pwa_router' (daemon.py:85) 但 pwa.py 只 export 'router'. "
            "ImportError 被 except 静默吞掉, PWA 7 endpoints 全 404. "
            "修复: 改 daemon.py 第 85 行 import 改为 `from sisoul.daemon_routes.pwa import router as pwa_router`"
        )
    assert r.status_code != 404


def test_p0_cli_restore_seed_integration_missing():
    """⚠️ P0: cli.py restore <seed> 走 stub 而非 dev-A run_restore_from_seed.

    dev-A report §5.1 明确要求改 cli.py 把 stub 替成 run_restore_from_seed,
    但 E2 主集成漏改. 当前用户 `sisoul restore <seed>` 实际跑 stub 报 'not implemented'.

    本测特意 assert 当前行为 (stub), 等主集成修后改成 assert 真恢复.
    修复方法 (主集成):

        # src/sisoul/cli.py 第 116-134 行替换:
        from sisoul.cli_commands.restore import run_restore_from_seed
        if seed:
            run_restore_from_seed(seed=seed, vault_dir=Path(vault_dir) if vault_dir else None,
                                  force=force)
        elif from_zip: ...
    """
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "HOME": str(Path(tmp) / "home")}
        Path(env["HOME"]).mkdir()
        seed = (
            "abandon abandon abandon abandon abandon abandon abandon abandon "
            "abandon abandon abandon about"
        )
        r = subprocess.run(
            ["sisoul", "restore", seed, "--vault-dir", str(Path(tmp) / "v2"), "--force"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        # 当前行为: rc=0 但 stdout 含 "not implemented"
        # 这是 P0 bug — 修后应改为 assert "vault 已恢复" in r.stdout
        is_stub = "not implemented" in r.stdout or "not-implemented" in r.stdout.lower()
        if is_stub:
            pytest.xfail(
                "P0 INTEGRATION BUG: cli.py restore <seed> 走 stub. "
                "主集成需改 cli.py 接 run_restore_from_seed (详 dev-A report §5.1)"
            )
        # 如已修, 应能恢复
        v2 = Path(tmp) / "v2"
        assert (v2 / "dna.json").exists(), "restore 后 dna.json 缺"
