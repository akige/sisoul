# sisoul contract deployments

| Contract | Network | Address | Tx | Date |
|---|---|---|---|---|
| PaymentChannel (feeBps=300) | OP Mainnet (10) | `0x3aa396d31872f87cf6269c65cf59bc00d820a19f` | `0xb950f6f0bba13a2d97af54b476d3393c60e6b5efb502f68b422ef5be88a0bc54` | 2026-06-11 |

- feeAddress/owner 当前 = 项目部署钱包; DAO 多签就位后 `setFeeAddress` 迁移 (feeBps 链上硬上限 500 不可改).
- 验证: `cast call 0x3aa396d31872f87cf6269c65cf59bc00d820a19f "feeBps()(uint16)" --rpc-url https://mainnet.optimism.io` → 300
