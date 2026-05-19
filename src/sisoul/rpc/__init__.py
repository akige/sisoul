"""sisoul · 链上 RPC 路由层 (v1.0-decentralized 模块 #4 Helios 集成).

§31 §2 模块 #4: Helios light client 内嵌. 走 Helios subprocess (`~/.cargo/bin/helios`)
启 local JSON-RPC server (默认 127.0.0.1:8545+), 客户端 HTTP 调用做 trustless
verification (Helios 内部 Merkle proof). 替换原 `eas.py` / `arweave.py` 里
对公共 RPC (`https://sepolia.optimism.io` 等) 的直连.

设计要点:
- subprocess.Popen 启 helios binary (不用 pyo3 binding, 避复杂 FFI)
- 自动检测 helios 在 $PATH / ~/.cargo/bin/helios; 缺失 → 抛 HeliosBinaryMissing
- 4 链支持矩阵 (helios 0.11.1):
  * ethereum mainnet (直接支持, --network mainnet)
  * ethereum sepolia (直接支持, --network sepolia)
  * opstack base / base-sepolia / op-mainnet / worldchain / zora / unichain
  * arbitrum / op-sepolia / zksync → helios 暂不直接支持, 退到 fallback
- fallback: helios 不可用 / chain 不支持 → fall back 到公共 RPC + 警告 banner
- mainnet head-only 模式: helios sync ETH mainnet head 是 0 gas readonly,
  跟 §I "不动实盘" 不冲突 (任务明示).

使用模式:
    client = HeliosClient(chains=["ethereum"], allow_fallback=True)
    await client.start()  # subprocess + wait sync 60s
    block = await client.eth_block_number("ethereum")
    await client.stop()
"""

from sisoul.rpc.helios_client import (
    DEFAULT_CONSENSUS_RPC,
    DEFAULT_EXECUTION_RPCS,
    HELIOS_NATIVE_CHAINS,
    ChainStatus,
    HeliosBinaryMissing,
    HeliosChainNotSupported,
    HeliosClient,
    HeliosError,
    HeliosSyncTimeout,
    find_helios_binary,
)

__all__ = [
    "HeliosClient",
    "HeliosError",
    "HeliosBinaryMissing",
    "HeliosSyncTimeout",
    "HeliosChainNotSupported",
    "ChainStatus",
    "find_helios_binary",
    "HELIOS_NATIVE_CHAINS",
    "DEFAULT_EXECUTION_RPCS",
    "DEFAULT_CONSENSUS_RPC",
]
