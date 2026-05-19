"""sisoul · Helios light client subprocess + HTTP smoke (Wave A agent-1 · #4).

真启 helios binary subprocess + sync ETH mainnet head + verify eth_blockNumber > 18M.
不打 mainnet tx (只读), 不依赖 Pinata / Alchemy / 公共 RPC 信任.

按 §J-2 反验收造假: 这个 file 是端到端真实流量, 不是 mock. fixture 跳过条件:
- helios binary 不存在 (CI 无 Rust toolchain) → skip
- SISOUL_HELIOS_SKIP_LIVE=1 (offline / 本地 quick dev) → skip
"""

from __future__ import annotations

import asyncio
import errno
import os
from pathlib import Path

import pytest

from sisoul.rpc.helios_client import (
    DEFAULT_EXECUTION_RPCS,
    HELIOS_NATIVE_CHAINS,
    ChainStatus,
    HeliosBinaryMissing,
    HeliosChainNotSupported,
    HeliosClient,
    HeliosError,
    find_helios_binary,
)


# pytestmark removed: 全局 asyncio mark 给非 async 函数会 warn.
# pyproject.toml 的 asyncio_mode='auto' 已自动 mark async 函数, 不需重复.


_HELIOS_BIN = find_helios_binary()
_SKIP_LIVE = os.environ.get("SISOUL_HELIOS_SKIP_LIVE") == "1" or _HELIOS_BIN is None
_LIVE_REASON = (
    "helios binary 未装 (装: cargo install --git https://github.com/a16z/helios "
    "--bin helios) 或 SISOUL_HELIOS_SKIP_LIVE=1"
)


# ─────────────────────────────────────────────────────────────────────────────
# 单元: 静态 helper (不需 helios)
# ─────────────────────────────────────────────────────────────────────────────


def test_find_helios_binary_returns_str_or_none() -> None:
    """find_helios_binary 必须返 abs path str 或 None, 不抛."""
    result = find_helios_binary()
    assert result is None or (isinstance(result, str) and Path(result).exists())


def test_helios_native_chains_table_includes_4_required() -> None:
    """§31 §2 模块 #4 要求: helios 至少能跑 ethereum / base-sepolia (op-mainnet/base 加分)."""
    assert "ethereum" in HELIOS_NATIVE_CHAINS
    assert "base-sepolia" in HELIOS_NATIVE_CHAINS
    assert "base" in HELIOS_NATIVE_CHAINS
    assert "op-mainnet" in HELIOS_NATIVE_CHAINS
    # arbitrum / op-sepolia / zksync 不直接支持 → 不应在表里 (走 fallback)
    assert "arbitrum-sepolia" not in HELIOS_NATIVE_CHAINS
    assert "optimism-sepolia" not in HELIOS_NATIVE_CHAINS


def test_default_execution_rpcs_match_chains() -> None:
    """每个 helios 原生 chain 应有默认 untrusted RPC."""
    for chain in ("ethereum", "base-sepolia", "base", "op-mainnet"):
        assert chain in DEFAULT_EXECUTION_RPCS
        assert len(DEFAULT_EXECUTION_RPCS[chain]) >= 1


# ─────────────────────────────────────────────────────────────────────────────
# 单元: HeliosClient (mock-mode, allow_fallback)
# ─────────────────────────────────────────────────────────────────────────────


async def test_helios_client_init_validates_empty_chains() -> None:
    with pytest.raises(HeliosError):
        HeliosClient(chains=[])


async def test_helios_client_init_chain_not_supported_hard_fail() -> None:
    """allow_fallback=False + unsupported chain → init 立即抛."""
    with pytest.raises(HeliosChainNotSupported):
        HeliosClient(chains=["arbitrum-sepolia"], allow_fallback=False)


async def test_helios_client_init_chain_not_supported_with_fallback_ok() -> None:
    """allow_fallback=True + unsupported chain → init 不抛 (启时降级)."""
    client = HeliosClient(
        chains=["arbitrum-sepolia"],
        allow_fallback=True,
        execution_rpcs={"arbitrum-sepolia": ["https://sepolia-rollup.arbitrum.io/rpc"]},
    )
    assert client.chains == ["arbitrum-sepolia"]


async def test_helios_client_start_fallback_when_no_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    """binary_path=None + allow_fallback=True → 全 chain 降 fallback, 不抛."""
    monkeypatch.setenv("SISOUL_HELIOS_SKIP_LIVE", "1")
    client = HeliosClient(
        chains=["ethereum"],
        binary_path=None,
        allow_fallback=True,
    )
    client.binary_path = None
    await client.start()
    s = client.status("ethereum")
    assert isinstance(s, ChainStatus)
    assert s.mode == "fallback"
    assert s.rpc_url is not None
    await client.stop()


async def test_helios_client_start_fallback_no_binary_no_allow_fails() -> None:
    """binary_path=None + allow_fallback=False → start 抛 HeliosBinaryMissing."""
    client = HeliosClient(chains=["ethereum"], binary_path="/nonexistent/helios", allow_fallback=False)
    client.binary_path = None
    with pytest.raises(HeliosBinaryMissing):
        await client.start()


async def test_helios_client_status_for_unstarted_chain() -> None:
    """status 在 start 之前: mode='stopped'."""
    client = HeliosClient(chains=["ethereum"], allow_fallback=True)
    s = client.status("ethereum")
    assert isinstance(s, ChainStatus)
    assert s.mode == "stopped"
    assert s.head == 0


async def test_helios_client_status_all_returns_dict() -> None:
    client = HeliosClient(chains=["ethereum", "base"], allow_fallback=True)
    all_s = client.status()
    assert isinstance(all_s, dict)
    assert set(all_s.keys()) == {"ethereum", "base"}


async def test_helios_call_unregistered_chain_raises() -> None:
    client = HeliosClient(chains=["ethereum"], allow_fallback=True)
    with pytest.raises(HeliosError):
        await client.call("nonexistent-chain", "eth_blockNumber", [])


async def test_helios_call_before_start_raises() -> None:
    """没 start 就 call → status.mode='stopped' → HeliosError."""
    client = HeliosClient(chains=["ethereum"], allow_fallback=True)
    with pytest.raises(HeliosError):
        await client.call("ethereum", "eth_blockNumber", [])


# ─────────────────────────────────────────────────────────────────────────────
# 单元: hex helper
# ─────────────────────────────────────────────────────────────────────────────


def test_hex_to_int_handles_none_zero_and_hex() -> None:
    from sisoul.rpc.helios_client import _hex_to_int
    assert _hex_to_int(None) == 0
    assert _hex_to_int("") == 0
    assert _hex_to_int("0x0") == 0
    assert _hex_to_int("0x10") == 16
    assert _hex_to_int(42) == 42
    assert _hex_to_int("0x17f76c9") == 25130697


def test_free_port_near_skips_used() -> None:
    from sisoul.rpc.helios_client import _free_port_near
    assert _free_port_near(8545) == 8545
    assert _free_port_near(8545, used=[8545]) == 8546
    assert _free_port_near(8545, used=[8545, 8546]) == 8547


# ─────────────────────────────────────────────────────────────────────────────
# fallback 模式 wait_synced 用 http mock (不真打公共 RPC)
# ─────────────────────────────────────────────────────────────────────────────


async def test_fallback_wait_synced_polls_public_rpc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fallback 模式 wait_synced 应试 1 次 eth_blockNumber 公共 RPC, 不真打."""
    call_log: list[tuple[str, str, list]] = []

    async def fake_raw(rpc_url: str, method: str, params: list, *, timeout: float = 15.0) -> str:
        call_log.append((rpc_url, method, params))
        return "0x1234567"  # 19088743 > 18M

    client = HeliosClient(chains=["ethereum"], allow_fallback=True, binary_path=None)
    client.binary_path = None
    await client.start()
    s = client.status("ethereum")
    assert s.mode == "fallback"

    monkeypatch.setattr(HeliosClient, "_raw_call", staticmethod(fake_raw))
    await client.wait_synced("ethereum", timeout=5.0)
    assert client.status("ethereum").head == 0x1234567
    assert client.status("ethereum").in_sync is True
    assert len(call_log) == 1
    assert call_log[0][1] == "eth_blockNumber"
    await client.stop()


# ─────────────────────────────────────────────────────────────────────────────
# 真 helios subprocess smoke (核心 §J-2 真验收 — sync mainnet, eth_blockNumber)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.skipif(_SKIP_LIVE, reason=_LIVE_REASON)
async def test_helios_live_ethereum_mainnet_sync(tmp_path: Path) -> None:
    """**核心真验收**: 启 helios ETH mainnet, sync ≤ 90s, eth_blockNumber > 18M.

    用例对应 §B.1.8 V1 + V2. 失败 → §J-2 验收造假 (本 file 端到端真打).
    """
    assert _HELIOS_BIN is not None, "fixture 应已 skip"
    client = HeliosClient(
        chains=["ethereum"],
        binary_path=_HELIOS_BIN,
        data_dir=tmp_path / "helios-data",
        log_dir=tmp_path / "helios-log",
        base_port=18545,
        sync_timeout_sec=120.0,
        allow_fallback=False,
        load_external_fallback=True,  # 实测关键 flag, 避 a16z 默认 endpoint 503
    )
    try:
        await client.start()
        await client.wait_synced("ethereum", timeout=120.0)
        s = client.status("ethereum")
        assert isinstance(s, ChainStatus)
        assert s.mode == "helios"
        assert s.in_sync is True
        assert s.head > 18_000_000, f"head 应 > 18M (mainnet 真 head), 实测 {s.head}"
        assert s.pid is not None
        assert s.rpc_url == "http://127.0.0.1:18545"
        block = await client.eth_block_number("ethereum")
        assert block >= s.head, f"block_number 应 >= 上次 head, {block} vs {s.head}"
    finally:
        await client.stop()
        await asyncio.sleep(0.5)
        s_after = client.status("ethereum")
        assert s_after.mode == "stopped"


@pytest.mark.skipif(_SKIP_LIVE, reason=_LIVE_REASON)
async def test_helios_live_subprocess_terminates_clean(tmp_path: Path) -> None:
    """启 + 立刻停, 验 subprocess 不留 zombie."""
    assert _HELIOS_BIN is not None
    client = HeliosClient(
        chains=["ethereum"],
        binary_path=_HELIOS_BIN,
        data_dir=tmp_path / "helios-data",
        log_dir=tmp_path / "helios-log",
        base_port=18646,
        sync_timeout_sec=10.0,
        allow_fallback=False,
    )
    await client.start()
    pid = client.status("ethereum").pid
    assert pid is not None
    await asyncio.sleep(1.0)
    await client.stop()
    await asyncio.sleep(0.5)
    try:
        os.kill(pid, 0)
        await asyncio.sleep(1.0)
        os.kill(pid, 0)
        pytest.fail(f"helios pid={pid} 在 stop 后仍存活")
    except OSError as e:
        assert e.errno == errno.ESRCH, f"unexpected errno {e.errno} for pid={pid}"
