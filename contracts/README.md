# sisoul · contracts (Phase 3 P3-4 DAO governance)

Foundry-based Solidity 合约: DAO governor + token + PIP/Skill registries.

## 装机

```bash
# 1. 装 Foundry
curl -L https://foundry.paradigm.xyz | bash && foundryup

# 2. 装依赖 (OpenZeppelin v5.x + forge-std)
cd contracts
forge install OpenZeppelin/openzeppelin-contracts@v5.0.2 --no-commit
forge install foundry-rs/forge-std --no-commit
```

## 合约清单

| 合约 | 文件 | 描述 |
|---|---|---|
| `SisoulToken` | `src/SisoulToken.sol` | ERC20Votes + Capped (1B SIS), MINTER_ROLE |
| `SisoulGov` | `src/SisoulGov.sol` | OZ Governor (7d voting, 4% quorum, 100 SIS threshold) |
| `PIPRegistry` | `src/PIPRegistry.sol` | PIP-001~004 + 未来 PIP on-chain spec CID 注册 |
| `SkillRegistry` | `src/SkillRegistry.sol` | AI skill (slug + IPFS/Arweave CID + version) 注册 |

Timelock: OZ `TimelockController` 2 days, Governor 拿 proposer/canceller role, executor=`address(0)`.

## 命令

```bash
forge build
forge test -vv
forge test --match-contract SisoulGovTest -vv
forge coverage
forge fmt

# 部署 (Optimism Sepolia 示例)
export PRIVATE_KEY=0x...
forge script script/Deploy.s.sol \
    --rpc-url optimism_sepolia \
    --broadcast \
    --private-key $PRIVATE_KEY \
    -vvvv
```

## 参数

- `votingDelay = 1 days` — propose → voting 开始
- `votingPeriod = 7 days` — voting 持续
- `proposalThreshold = 100 SIS` — propose 最低 self-delegate
- `quorumFraction = 4%` — 通过最低投票
- `timelockDelay = 2 days` — Succeeded → Executed 最低延迟
- clock = `mode=timestamp` (跨链一致, 兼容 L2)

## 测试覆盖

- `SisoulGov.t.sol` 23 cases (param / propose / vote / quorum / queue / execute / cancel / token)
- `PIPRegistry.t.sol` 13 cases (status FSM / spec freeze / supersede / enumerate)
- `SkillRegistry.t.sol` 12 cases (register / update / freeze / transfer)

Python 侧 web3.py 调用见 `src/sisoul/dao/`.
