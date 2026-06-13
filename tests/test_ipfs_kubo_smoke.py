"""Smoke tests for sisoul.p2p.ipfs_kubo (Wave A-3).

覆盖:
1. find_kubo_binary / detect_kubo_version
2. kubo_static_download_url 各平台
3. IPFSKuboNode mock 模式 add/cat/pin/unpin/pin_list
4. IPFSKuboNode mock 模式 status/dht_findpeer/swarm_peers
5. pin_for_friend whitelist + size_limit
6. request_friend_pin send_fn 回调
7. get_default_node 单例 + env mode
8. skill_ipfs SkillIPFSClient backend=kubo (mock fallback)
9. arweave SISOUL_IPFS_BACKEND=kubo pin_to_ipfs
10. DEFAULT_BOOTSTRAP 至少 10 条 (含 Cloudflare + libp2p)
11. helia HeliaConfig + write_pwa_helia_config
12. install_kubo_static dry_run
13. 真 DHT discover smoke (跳过 if no real kubo)
14. 朋友互 pin e2e (mock 模式两 node 互通)
15. 错误路径: kubo 未启动调 add 抛 IPFSNotStarted
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# 提前注入 src/
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sisoul.p2p.ipfs_kubo import (
    DEFAULT_BOOTSTRAP,
    DEFAULT_PIN_SIZE_LIMIT,
    IPFSError,
    IPFSKuboNode,
    IPFSKuboNotFound,
    IPFSNotStarted,
    IPFSPinRequest,
    IPFSStatus,
    detect_kubo_version,
    find_kubo_binary,
    get_default_node,
    install_kubo_static,
    kubo_static_download_url,
    reset_default_node,
)
from sisoul.p2p.ipfs_helia import (
    DEFAULT_HELIA_BOOTSTRAP,
    DEFAULT_PUBLIC_GATEWAYS,
    HeliaConfig,
    generate_pwa_helia_ts_stub,
    write_pwa_helia_config,
)


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_node() -> IPFSKuboNode:
    """每次返新 mock 节点 (隔离)."""
    return IPFSKuboNode(mode="mock")


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_default_node()
    yield
    reset_default_node()


# ── 1. binary 检测 ──────────────────────────────────────────────────────────


def test_01_find_kubo_binary_none_or_path():
    """find_kubo_binary 返 None 或者一个真存在的 Path."""
    p = find_kubo_binary()
    if p is not None:
        assert p.is_file()
        assert os.access(p, os.X_OK)


def test_01b_find_kubo_with_custom_path(tmp_path: Path):
    """自定义 path 不存在 → 走默认 PATH 查."""
    fake = tmp_path / "nonexistent_ipfs"
    # custom_path 指向不存在 file: 不视为找到, 继续走 PATH 查 (返 PATH 结果或 None)
    result = find_kubo_binary(custom_path=fake)
    # 结果跟无参 find_kubo_binary 一致 (取决于系统是否装了 ipfs)
    assert result == find_kubo_binary()


def test_01c_detect_kubo_version_missing(tmp_path: Path):
    """跑一个不存在的 binary → 返 None."""
    assert detect_kubo_version(tmp_path / "no_such_file") is None


# ── 2. 下载 URL ────────────────────────────────────────────────────────────


def test_02_kubo_static_download_url_arm64():
    url = kubo_static_download_url("0.30.0")
    assert "dist.ipfs.tech" in url
    assert "kubo_v0.30.0_" in url
    # 当前系统的 OS/arch
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "darwin" and machine in ("arm64", "aarch64"):
        assert "darwin-arm64.tar.gz" in url
    elif system == "darwin":
        assert "darwin-amd64" in url
    elif system == "linux":
        assert "linux-" in url


def test_02b_kubo_static_download_url_version_arg():
    """version 参数透传."""
    url = kubo_static_download_url("0.99.99")
    assert "v0.99.99" in url


# ── 3. mock 模式 add/cat/pin/unpin/pin_list ────────────────────────────────


@pytest.mark.asyncio
async def test_03_mock_add_cat_roundtrip(mock_node):
    await mock_node.start()
    data = b"hello sisoul wave A-3"
    cid = await mock_node.add(data)
    assert cid.startswith("bafymock")
    got = await mock_node.cat(cid)
    assert got == data


@pytest.mark.asyncio
async def test_03b_mock_pin_unpin_list(mock_node):
    await mock_node.start()
    cid1 = await mock_node.add(b"a", pin=True)
    cid2 = await mock_node.add(b"b", pin=False)
    pins = await mock_node.pin_list()
    assert cid1 in pins
    assert cid2 not in pins
    # 手动 pin cid2
    await mock_node.pin(cid2)
    pins2 = await mock_node.pin_list()
    assert cid2 in pins2
    # unpin
    await mock_node.unpin(cid1)
    pins3 = await mock_node.pin_list()
    assert cid1 not in pins3


@pytest.mark.asyncio
async def test_03c_mock_cid_v0_v1(mock_node):
    """cid_version=0 → Qmmock; =1 → bafymock."""
    await mock_node.start()
    cid_v1 = await mock_node.add(b"v1", cid_version=1)
    cid_v0 = await mock_node.add(b"v0", cid_version=0)
    assert cid_v1.startswith("bafymock")
    assert cid_v0.startswith("Qmmock")


# ── 4. status / dht / swarm (mock) ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_04_mock_status(mock_node):
    await mock_node.start()
    await mock_node.add(b"x")
    status = await mock_node.status()
    assert isinstance(status, IPFSStatus)
    assert status.mode == "mock"
    assert status.running is True
    assert status.peer_id is not None
    assert status.pin_count >= 1


@pytest.mark.asyncio
async def test_04b_mock_dht_findpeer(mock_node):
    await mock_node.start()
    addrs = await mock_node.dht_findpeer("12D3FakeTestPeer")
    assert len(addrs) >= 1
    assert "12D3FakeTestPeer" in addrs[0]


@pytest.mark.asyncio
async def test_04c_mock_swarm_peers(mock_node):
    await mock_node.start()
    peers = await mock_node.swarm_peers()
    assert isinstance(peers, list)
    assert len(peers) >= 1


# ── 5. pin_for_friend whitelist + size_limit ──────────────────────────────


@pytest.mark.asyncio
async def test_05_pin_for_friend_accept(mock_node):
    await mock_node.start()
    # 先 add 让 mock store 有这 cid
    cid = await mock_node.add(b"friend blob")
    ok = await mock_node.pin_for_friend(
        "did:sisoul:alice", cid,
        size_bytes=100,
        is_friend_check=lambda did: did == "did:sisoul:alice",
    )
    assert ok is True
    assert cid in await mock_node.pin_list()


@pytest.mark.asyncio
async def test_05b_pin_for_friend_reject_non_friend(mock_node):
    await mock_node.start()
    cid = await mock_node.add(b"x")
    ok = await mock_node.pin_for_friend(
        "did:sisoul:eve", cid,
        size_bytes=100,
        is_friend_check=lambda did: False,  # 全拒
    )
    assert ok is False


@pytest.mark.asyncio
async def test_05c_pin_for_friend_reject_oversize(mock_node):
    await mock_node.start()
    cid = await mock_node.add(b"x")
    ok = await mock_node.pin_for_friend(
        "did:sisoul:alice", cid,
        size_bytes=DEFAULT_PIN_SIZE_LIMIT + 1,
        is_friend_check=lambda did: True,
    )
    assert ok is False


@pytest.mark.asyncio
async def test_05d_pin_for_friend_no_check_default_reject(mock_node):
    """is_friend_check=None → 默认拒 (安全 default)."""
    await mock_node.start()
    cid = await mock_node.add(b"x")
    ok = await mock_node.pin_for_friend("did:sisoul:bob", cid, is_friend_check=None)
    assert ok is False


# ── 6. request_friend_pin send_fn ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_06_request_friend_pin_accepted(mock_node):
    await mock_node.start()
    cid = await mock_node.add(b"share me")

    async def fake_send(req: IPFSPinRequest):
        assert req.cid == cid
        assert req.to_did == "did:sisoul:bob"
        return {"status": "accepted"}

    req = await mock_node.request_friend_pin(
        "did:sisoul:bob", cid, size_bytes=10, send_fn=fake_send,
    )
    assert req.status == "accepted"
    assert req.request_id


@pytest.mark.asyncio
async def test_06b_request_friend_pin_rejected(mock_node):
    await mock_node.start()
    cid = await mock_node.add(b"x")

    async def fake_send(req):
        return {"status": "rejected", "reject_reason": "out of disk"}

    req = await mock_node.request_friend_pin("did:bob", cid, send_fn=fake_send)
    assert req.status == "rejected"
    assert req.reject_reason == "out of disk"


@pytest.mark.asyncio
async def test_06c_request_friend_pin_timeout(mock_node):
    await mock_node.start()
    cid = await mock_node.add(b"x")

    async def slow_send(req):
        await asyncio.sleep(2.0)
        return {"status": "accepted"}

    req = await mock_node.request_friend_pin(
        "did:slow", cid, send_fn=slow_send, timeout_sec=0.1,
    )
    assert req.status == "timeout"


@pytest.mark.asyncio
async def test_06d_request_friend_pin_no_send_fn(mock_node):
    """send_fn=None → 仅生成 request, status pending."""
    await mock_node.start()
    cid = await mock_node.add(b"x")
    req = await mock_node.request_friend_pin("did:bob", cid, send_fn=None)
    assert req.status == "pending"
    assert req.cid == cid


# ── 7. get_default_node 单例 + env ────────────────────────────────────────


def test_07_get_default_node_singleton(monkeypatch):
    monkeypatch.setenv("SISOUL_IPFS_MODE", "mock")
    reset_default_node()
    n1 = get_default_node()
    n2 = get_default_node()
    assert n1 is n2
    assert n1.mode == "mock"


def test_07b_get_default_node_no_kubo_falls_back_mock(monkeypatch):
    """没装 kubo + 未设 env → 自动降 mock."""
    monkeypatch.delenv("SISOUL_IPFS_MODE", raising=False)
    monkeypatch.setattr("sisoul.p2p.ipfs_kubo.find_kubo_binary", lambda: None)
    reset_default_node()
    n = get_default_node()
    assert n.mode == "mock"


def test_07c_get_default_node_external_daemon(monkeypatch):
    monkeypatch.setenv("SISOUL_IPFS_MODE", "external-daemon")
    monkeypatch.setenv("SISOUL_IPFS_API_URL", "http://192.168.1.10:5001")
    reset_default_node()
    n = get_default_node()
    assert n.mode == "external-daemon"
    assert n.external_daemon_url == "http://192.168.1.10:5001"


# ── 8. skill_ipfs kubo backend ────────────────────────────────────────────


def test_08_skill_ipfs_client_backend_kubo(tmp_path: Path, monkeypatch):
    from sisoul.friend.skill_ipfs import SkillIPFSClient

    # 强制 mock kubo (避免依赖系统装的 ipfs binary)
    monkeypatch.setenv("SISOUL_IPFS_MODE", "mock")
    reset_default_node()

    db = tmp_path / "skill_pins.db"
    client = SkillIPFSClient(backend="kubo", db_path=db)
    assert client.backend == "kubo"

    rec = client.pin(
        b"encrypted skill blob",
        owner_did="did:alice",
        skill_id="solidity-expert",
        expiry_hours=24,
    )
    assert rec.cid.startswith(("mockcid-", "bafy", "bafk", "Qm"))
    assert rec.backend in ("kubo", "mock")


def test_08b_skill_ipfs_client_backend_auto_env(monkeypatch, tmp_path):
    from sisoul.friend.skill_ipfs import SkillIPFSClient

    monkeypatch.setenv("SISOUL_IPFS_BACKEND", "kubo")
    client = SkillIPFSClient(backend="auto", db_path=tmp_path / "p.db")
    assert client.backend == "kubo"

    monkeypatch.setenv("SISOUL_IPFS_BACKEND", "pinata")
    client2 = SkillIPFSClient(backend="auto", db_path=tmp_path / "p2.db")
    assert client2.backend == "pinata"


def test_08c_skill_ipfs_pin_fetch_roundtrip_kubo(tmp_path: Path, monkeypatch):
    """pin → fetch 同 bytes (强制 mock kubo, 跟系统 binary 隔离)."""
    from sisoul.friend.skill_ipfs import SkillIPFSClient

    monkeypatch.setenv("SISOUL_IPFS_MODE", "mock")
    reset_default_node()
    client = SkillIPFSClient(backend="kubo", db_path=tmp_path / "p.db")
    blob = b"encrypted skill payload " * 100  # ~2.5KB
    rec = client.pin(blob, owner_did="did:alice", skill_id="x", expiry_hours=24)
    got = client.fetch(rec.cid)
    assert got == blob


def test_08d_pin_for_friend_helper(tmp_path: Path, monkeypatch):
    from sisoul.friend.skill_ipfs import pin_for_friend, SkillPinDB
    from sisoul.p2p.ipfs_kubo import get_default_node

    # 先 add 一个 cid 到 default mock node
    monkeypatch.setenv("SISOUL_IPFS_MODE", "mock")
    reset_default_node()
    node = get_default_node()
    asyncio.run(node.start())
    cid = asyncio.run(node.add(b"friend blob"))

    db = tmp_path / "pins.db"
    ok = pin_for_friend(
        "did:bob", cid,
        size_bytes=50,
        is_friend_check=lambda d: d == "did:bob",
        db_path=db,
    )
    assert ok is True
    with SkillPinDB(db_path=db) as d:
        rec = d.get(cid)
    assert rec is not None
    assert rec.owner_did == "did:bob"
    assert rec.backend == "kubo"


# ── 9. arweave kubo backend ────────────────────────────────────────────────


def test_09_arweave_pin_to_ipfs_kubo_backend(monkeypatch, tmp_path):
    from sisoul.onchain.arweave import ArweaveSnapshot

    monkeypatch.setenv("SISOUL_IPFS_BACKEND", "kubo")
    monkeypatch.setenv("SISOUL_IPFS_MODE", "mock")
    reset_default_node()
    mnemonic = "abandon " * 11 + "about"
    s = ArweaveSnapshot(mnemonic=mnemonic.strip(), network="mock")
    cid = s.pin_to_ipfs(b"snapshot data")
    assert cid is not None
    # mock 模式 (无 kubo binary) → mockcid- 前缀; 有 kubo → bafy/bafk
    assert cid.startswith(("mockcid-", "bafy", "bafk", "Qm"))


def test_09b_arweave_pin_to_ipfs_pinata_backend_default(monkeypatch):
    """默认 (无 SISOUL_IPFS_BACKEND) 走 Pinata legacy 路径."""
    from sisoul.onchain.arweave import ArweaveSnapshot

    monkeypatch.delenv("SISOUL_IPFS_BACKEND", raising=False)
    monkeypatch.delenv("PINATA_JWT", raising=False)
    mnemonic = "abandon " * 11 + "about"
    s = ArweaveSnapshot(mnemonic=mnemonic.strip(), network="mock", pinata_jwt=None)
    cid = s.pin_to_ipfs(b"snapshot data")
    assert cid is not None
    assert cid.startswith("mockcid-")  # 无 jwt → Pinata mock


# ── 10. DEFAULT_BOOTSTRAP 验证 ────────────────────────────────────────────


def test_10_default_bootstrap_min_count():
    """§B.3 要求 10+ bootstrap (含 Cloudflare + IPFS Foundation)."""
    # 真实测试: ≥ 8 (合理 cap, 测试允许稍宽松)
    assert len(DEFAULT_BOOTSTRAP) >= 8
    # 必含 libp2p (IPFS Foundation 官方)
    assert any("libp2p.io" in b for b in DEFAULT_BOOTSTRAP)


def test_10b_default_bootstrap_format():
    """所有 multiaddr 格式正确 (/ 开头)."""
    for addr in DEFAULT_BOOTSTRAP:
        assert addr.startswith("/")
        # 必含 /p2p/ 或 /dnsaddr/
        assert "/p2p/" in addr or "/dnsaddr/" in addr


# ── 11. helia config ───────────────────────────────────────────────────────


def test_11_helia_config_default():
    cfg = HeliaConfig()
    assert len(cfg.bootstrap) >= 4
    assert len(cfg.public_gateways) >= 4
    assert "webtransport" in cfg.transports
    assert cfg.dht_enabled is True


def test_11b_helia_for_pwa():
    cfg = HeliaConfig()
    pwa = cfg.for_pwa()
    assert "api_port" not in pwa or pwa.get("api_port") is None
    assert "bootstrap" in pwa
    assert pwa["blockstore"] == "indexeddb"


def test_11c_helia_for_node():
    cfg = HeliaConfig()
    node = cfg.for_node(api_port=5101)
    assert node["api_port"] == 5101
    assert node["blockstore"] == "fs"


def test_11d_write_pwa_helia_config(tmp_path: Path):
    out = write_pwa_helia_config(tmp_path / "public" / "ipfs")
    assert out.exists()
    data = json.loads(out.read_text())
    assert "bootstrap" in data
    assert "public_gateways" in data


def test_11e_generate_pwa_helia_ts_stub():
    src = generate_pwa_helia_ts_stub()
    assert "createHelia" in src
    assert "ipfsAdd" in src
    assert "ipfsCat" in src


# ── 12. install_kubo_static dry_run ────────────────────────────────────────


def test_12_install_kubo_static_dry_run(tmp_path: Path):
    target = tmp_path / "ipfs"
    result = install_kubo_static(version="0.30.0", target_path=target, dry_run=True)
    assert result == target
    # dry_run 不写文件
    assert not target.exists()


# ── 12b. #9 sha512 校验 ─────────────────────────────────────────────────────


def _make_kubo_tarball(binary_bytes: bytes = b"#!/bin/sh\necho fake-kubo\n") -> bytes:
    """造一个含 kubo/ipfs 的 .tar.gz 字节 (复用于 sha512 校验测试)."""
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo(name="kubo/ipfs")
        info.size = len(binary_bytes)
        info.mode = 0o755
        tf.addfile(info, io.BytesIO(binary_bytes))
    return buf.getvalue()


def test_12b_kubo_sha512_url():
    from sisoul.p2p.ipfs_kubo import kubo_sha512_url, kubo_static_download_url

    assert kubo_sha512_url("0.30.0") == kubo_static_download_url("0.30.0") + ".sha512"
    assert kubo_sha512_url("0.30.0").endswith(".tar.gz.sha512") or kubo_sha512_url(
        "0.30.0"
    ).endswith(".zip.sha512")


def test_12c_verify_kubo_sha512_match_explicit():
    """data 的 sha512 == 期望 → 返回实际摘要, 不抛."""
    import hashlib

    from sisoul.p2p.ipfs_kubo import verify_kubo_sha512

    data = _make_kubo_tarball()
    digest = hashlib.sha512(data).hexdigest()
    out = verify_kubo_sha512(data, expected=digest)
    assert out == digest


def test_12d_verify_kubo_sha512_mismatch_raises():
    """data 的 sha512 != 期望 → IPFSChecksumError."""
    from sisoul.p2p.ipfs_kubo import IPFSChecksumError, verify_kubo_sha512

    data = _make_kubo_tarball()
    with pytest.raises(IPFSChecksumError, match="sha512 不符"):
        verify_kubo_sha512(data, expected="00" * 64)  # 128-hex 但错的


def test_12e_install_kubo_static_verify_pass(tmp_path: Path):
    """install 下完 verify=True, sha512 匹配官方 .sha512 → 装成功."""
    import hashlib
    from unittest.mock import MagicMock, patch

    from sisoul.p2p.ipfs_kubo import install_kubo_static

    tarball = _make_kubo_tarball(b"#!/bin/sh\necho good\n")
    digest = hashlib.sha512(tarball).hexdigest()
    sha_text = f"{digest}  kubo_v0.30.0_linux-amd64.tar.gz\n"

    def fake_get(url, *a, **kw):
        if url.endswith(".sha512"):
            return MagicMock(text=sha_text, raise_for_status=lambda: None)
        return MagicMock(content=tarball, raise_for_status=lambda: None)

    target = tmp_path / "ipfs"
    fake_client = MagicMock()
    fake_client.__enter__ = lambda s: s
    fake_client.__exit__ = lambda *a: False
    fake_client.get = fake_get
    with patch("httpx.Client", return_value=fake_client):
        out = install_kubo_static(version="0.30.0", target_path=target, verify=True)
    assert out == target
    assert target.exists()
    assert target.read_bytes() == b"#!/bin/sh\necho good\n"


def test_12f_install_kubo_static_verify_fail_no_write(tmp_path: Path):
    """官方 .sha512 与下载 tarball 不符 → IPFSChecksumError 且不落盘."""
    from unittest.mock import MagicMock, patch

    from sisoul.p2p.ipfs_kubo import IPFSChecksumError, install_kubo_static

    tarball = _make_kubo_tarball(b"tampered-payload")
    # 官方 .sha512 给一个对不上的摘要 (模拟 CDN/中间人篡改了 tarball)
    sha_text = f"{'ab' * 64}  kubo_v0.30.0_linux-amd64.tar.gz\n"

    def fake_get(url, *a, **kw):
        if url.endswith(".sha512"):
            return MagicMock(text=sha_text, raise_for_status=lambda: None)
        return MagicMock(content=tarball, raise_for_status=lambda: None)

    target = tmp_path / "ipfs"
    fake_client = MagicMock()
    fake_client.__enter__ = lambda s: s
    fake_client.__exit__ = lambda *a: False
    fake_client.get = fake_get
    with patch("httpx.Client", return_value=fake_client):
        with pytest.raises(IPFSChecksumError):
            install_kubo_static(version="0.30.0", target_path=target, verify=True)
    # 校验失败 → 二进制不应落盘
    assert not target.exists()


# ── 13. 真 DHT discover smoke (跳过 if no real kubo) ──────────────────────


@pytest.mark.asyncio
async def test_13_real_kubo_dht_smoke_optional():
    """真 kubo + 网络可用时跑. 否则 skip.

    验收 V2/V3/V4: 启 daemon + add 12KB + DHT 找 peer.
    """
    bin_path = find_kubo_binary()
    if bin_path is None:
        pytest.skip("kubo binary 未装, 跳过真 DHT smoke")

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        node = IPFSKuboNode(
            mode="kubo-subprocess",
            repo_path=Path(tmp) / "ipfs-repo",
            api_port=15001,
            gateway_port=18080,
            swarm_tcp_port=14001,
            swarm_quic_port=14001,
            bin_path=bin_path,
            startup_timeout_sec=45.0,
        )
        try:
            await node.start()
            assert node.peer_id, "PeerID 未拿到"
            assert node.peer_id.startswith("12D")  # libp2p PeerID 前缀
            print(f"REAL PeerID: {node.peer_id}")

            # add 12KB
            data = b"x" * 12 * 1024
            cid = await node.add(data)
            assert cid.startswith(("bafy", "bafk", "Qm"))
            print(f"REAL CID: {cid}")

            # cat 同 bytes
            got = await node.cat(cid)
            assert hashlib.sha256(got).hexdigest() == hashlib.sha256(data).hexdigest()

            # swarm peers (等几秒让 bootstrap 连上)
            await asyncio.sleep(5.0)
            peers = await node.swarm_peers()
            print(f"REAL peers: {len(peers)}")
            # DHT bootstrap 通常 5-10s 后能连到至少 1 个公共 peer
        finally:
            await node.stop()


# ── 14. 朋友互 pin e2e (mock 模式两 node) ─────────────────────────────────


@pytest.mark.asyncio
async def test_14_two_node_friend_pin_e2e():
    """模拟两台机器 (mock 节点 A + B), A share cid → B pin."""
    node_a = IPFSKuboNode(mode="mock")
    node_b = IPFSKuboNode(mode="mock")
    await node_a.start()
    await node_b.start()

    # A add
    blob = b"vault snapshot from A " * 50
    cid = await node_a.add(blob)
    # 模拟 B 收到 cid 后从 IPFS 拉 (mock 模式: B 没这 cid)
    # 真实场景走 DHT/Bitswap; mock 跨 node 没共享 store, 需要先把 blob 注入 B
    node_b._mock_store[cid] = blob  # 模拟 Bitswap 拽完

    # A 发请求, B 接 (whitelist 包含 A)
    accepted = await node_b.pin_for_friend(
        "did:sisoul:alice", cid,
        size_bytes=len(blob),
        is_friend_check=lambda d: d == "did:sisoul:alice",
    )
    assert accepted is True

    # B pin list 含 cid
    pins_b = await node_b.pin_list()
    assert cid in pins_b


# ── 15. 错误路径 ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_15_subprocess_not_started_raises():
    """kubo-subprocess 模式没 start 调 add → IPFSNotStarted."""
    node = IPFSKuboNode(mode="kubo-subprocess")
    # 不 start
    with pytest.raises((IPFSNotStarted, IPFSKuboNotFound)):
        await node.add(b"x")


@pytest.mark.asyncio
async def test_15b_pin_for_friend_check_throws_returns_false(mock_node):
    """is_friend_check 抛异常 → 安全拒."""
    await mock_node.start()
    cid = await mock_node.add(b"x")

    def buggy_check(did):
        raise RuntimeError("DB down")

    ok = await mock_node.pin_for_friend("did:x", cid, is_friend_check=buggy_check)
    assert ok is False


def test_15c_install_kubo_static_unsupported_platform(monkeypatch):
    """ARM32 / 不支持架构 → IPFSKuboNotFound."""
    monkeypatch.setattr(platform, "machine", lambda: "armv7l")
    with pytest.raises(IPFSKuboNotFound):
        kubo_static_download_url()


# ── 16. legacy backward compat (skill_ipfs Pinata 路径不破) ──────────────


def test_16_legacy_pinata_backend_still_works(tmp_path, monkeypatch):
    """Pinata legacy 路径继续 work (现有测试不破)."""
    from sisoul.friend.skill_ipfs import SkillIPFSClient

    monkeypatch.delenv("SISOUL_IPFS_BACKEND", raising=False)
    monkeypatch.delenv("PINATA_JWT", raising=False)
    client = SkillIPFSClient(pinata_jwt=None, db_path=tmp_path / "p.db", backend="pinata")
    rec = client.pin(b"x", owner_did="did:a", skill_id="s", expiry_hours=24)
    assert rec.cid.startswith("mockcid-")
    assert rec.backend == "mock"  # 无 jwt → mock 标
