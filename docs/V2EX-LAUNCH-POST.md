# V2EX 发布文 (v2 · 2026-06-10 按"借额度不借账号"卖点重写)

> 标题: `[分享] sisoul · 把你的 Claude/GPT 额度借给朋友, 他看不到你的 key, 你看不到他的 prompt · 开源 · 0 服务器 · 永不发币`

---

V 站隔三差五就有"求合租 Claude / 求拼 API"的帖子, 但合租只有两条路, 都难受:

- **直接给 key** — 对方能看你账单、能干任何事, 你裸奔
- **进合租中转站** — 你的每一条 prompt 都过站长的服务器, 信不信由你, 跑路看缘分

我花 7 个月写了第三条路, 开源了: **sisoul** — 借出去的是**一次次加密转发的回复**, 不是账号。

> 一句话: 中心化共享 LLM 你得信运营方; sisoul 你只需要信你朋友 — 而信任边界、额度、批准权全在你自己手里。

仓还 0 star, 你来就是第一波 alpha。

## 跟"给 key / 合租中转"的真区别

| | 直接给 key | 合租中转站 | sisoul borrow |
|---|---|---|---|
| 对方拿到什么 | 你的 key 本体 | 站长发的子 key | 一次加密转发的回复 |
| 你的 key 在哪 | 对方手里 | 站长服务器 | **不出你的机器** |
| 谁能看 prompt | — | 站长技术上全能看 | **借出方也看不到** (E2E 加密, 内存解密即转发, 不落盘) |
| 额度控制 | 无 | 站长说了算 | 你逐好友设: 月上限 / 频率 / 自动批 or 逐次批 |
| 中间商抽成 | — | 有 | **0** (要收钱也是好友间直转 USDT, 我们不碰) |
| 跑路风险 | — | 有 | 没有"我们"可跑 — 0 服务器, 卸载即消失 |

技术上诚实声明可信边界: prompt 用 libsodium Box (X25519, 密钥就是 did:key 本体) 端到端加密, 借出方 daemon 只在内存解密后转给 LLM, 不落盘不进 log (`src/sisoul/friend/encrypted_proxy.py` 有 `enforce_no_disk_write` 自检, 代码可审)。但借出方**机器**本身是可信边界 — 它毕竟要把明文发给 LLM API。防君子审小人, 不吹"绝对零知识"。

## 5 分钟真上手 (全部真测过)

```bash
# 装 (auto 探测 OS + Python + kubo, no sudo)
curl -sSL https://raw.githubusercontent.com/akige/sisoul/main/install.sh | bash

sisoul init --goals "..."          # 12 词 BIP-39 → did:key 身份, 0 注册 0 gas
sisoul daemon &                    # 起本机 daemon
# 浏览器开 http://127.0.0.1:9876/app/  → 全功能 dashboard
```

**找一个朋友, 两台机器:**

```bash
sisoul friend qr                   # 生 QR 给朋友扫 (或直接粘 did:key / 局域网 mDNS 发现)
sisoul friend add did:key:z6...    # 双方互加
sisoul perms set <对方did> --mode strong-tie-auto --monthly-cap 1000000   # 借出方设额度
```

然后在 PWA 的 Borrow 页:

1. 选朋友 → 填 token 数 + prompt → 选审批模式
2. **"等对方批准"模式**: 对方 Lend 页**实时弹出**你的请求卡片 (SSE 推送, 不用刷新), 点 Approve 你立刻拿到回复, 点 Deny 你立刻看到理由
3. **"自动批准"模式**: 强关系预授权, 秒回
4. 回复直接渲染在卡片里 — 整条链路: 你的 prompt 加密 → GossipSub P2P → 对方 daemon 内存解密 → 调**对方自己配的** LLM endpoint → 加密回传

非好友 (任何陌生 did) 来借 → daemon 直接拒, 烧不了你的配额。

顺手还有 **Signal 级 E2E chat** (Double Ratchet + PQXDH): `sisoul chat send <did> "hi"` — 跟借 LLM 同一套好友关系和密钥, 不用再注册一个 IM。

## 借 LLM 的成本设计 (3 档, docs/INCENTIVE-DESIGN.md)

| 场景 | 模式 | 借出方拿 | 借入方付 |
|---|---|---|---|
| 真朋友 | gift (默认) | reputation + 社交感谢 | 0 |
| 网友 | kudos | 计数 +N (不可转, 5%/月衰减) | 计数 -N |
| 陌生人/紧急 | USDT-TRC20 micropay | USDT 直接到自己钱包 | ~0.01 USDT/1k token |

**sisoul 抽 0%** — 借入方直接打钱给借出方, 我们不托管不路由。

## 诚实分级: 今天有什么 / 没什么

✅ 已 ship 且真测:

- 一行 install.sh (mac/linux/WSL2) + Homebrew formula + macOS 菜单栏 app (29MB Sisoul.app)
- PWA dashboard 14 页 (daemon 本机 serve, 也有 [GitHub Pages 镜像](https://akige.github.io/sisoul/))
- 借 LLM 加密真转发 + per-request 实时审批 (上面那套, 2026-06-10 全链路 e2e 过)
- E2E chat / kudos / USDT micropay (dry-run quote) / USDT 到账自动批 (TronGrid 轮询)
- 链上 username: `@akige` 已在 Optimism mainnet EAS 注册 ([tx](https://optimistic.etherscan.io/tx/0xabcb1bab93946d491503a6e1368ee8c6b870085e185eed83f629459d865bb72c) · [attestation](https://optimism.easscan.org/attestation/view/0x78375e7ed6cbec575f630be8e32377da91de4801e6f9799bfb16a7c71ca6acdb)), `sisoul username resolve akige` 任何人可验
- 更新通知: daemon 自查新版 → PWA 角标 + `sisoul update` 一键升级 (git pull, 不经任何我们的服务器)

❌ 还没有 (别骂, 先说了):

- iOS / Android native app (skeleton 有, 没 .ipa/.apk, T+1m~T+2m)
- 跨 NAT 的 P2P 实测规模数据 (协议栈是 kubo DHT + AutoNAT + Circuit Relay, 同网段/同主机真测过, 大规模跨 NAT 成功率就靠你们 alpha 帮我踩)

🟡 中心化残留 (诚实标注, 但**没有一个在我们手里**):

- 上游 LLM 是商业 SaaS — 你也可以把 endpoint 指本地 Ollama, 那就 100% 离线
- 首次入网用 IPFS 官方公共 bootstrap 节点 (可自配)
- `@username` 解析默认走 easscan 公共 indexer (数据在链上, 谁都能自建)
- 代码分发走 GitHub

## 适合谁

- **结对/小圈子开发者**: 一人有 Claude/GPT 高配, 朋友想用不想绑卡 — borrow 就是为这造的
- **隐私敏感型**: 不接受 prompt 过第三方中转、想审计每行转发代码的人
- **自托管党**: 已经在跑 Ollama/NAS, 认"0 SaaS、卸载即消失"
- **被发币项目伤过的去中心化爱好者**: 见下面三原则

**不适合**: 纯小白 (要装 Python + kubo、要保管 12 词助记词)、只用手机的、要稳定 SLA 的。这是 alpha, 招的是极客测试员, 不是替代 ChatGPT。

## 3 个硬原则

- **永不发币** (白皮书 §4.10)。Tor 22 年没 token, Linux 33 年。`docs/GOVERNANCE.md` 全文公开 funding 模型。不接受请绕道。
- **永不下架**。0 服务器架构, 我家断电协议还在你们机器之间跑。
- **没空投、没积分**。早期 alpha 只有不可转的 Soulbound Badge 留念 (0 经济价值)。

## 谁的服务器在支撑?

你们自己的。每个 sisoul 节点 = 自带 daemon + kubo IPFS 节点, 消息走 GossipSub P2P mesh, LLM 调用走借出方自己的 key。项目方这边**没有任何一台服务器在用户路径上** — 1 万人在线, 我们这边负载是 0, 因为没有"我们这边"。(分发和更新检查走 GitHub CDN, 那是微软的事。)

## 装机要求

Python 3.11+ · macOS / Linux / WSL2 (完整步骤 [docs/INSTALL.md](https://github.com/akige/sisoul/blob/main/docs/INSTALL.md), 卡住先看 [排错手册](https://github.com/akige/sisoul/blob/main/docs/TROUBLESHOOTING.md))

## 支持开发 (grants + 捐助, 不发币)

- USDT (TRC20): `TNesE1mJZ11ogsrPC7tsG2he7UJ7iKSmKn` ([tronscan 可查](https://tronscan.org/#/address/TNesE1mJZ11ogsrPC7tsG2he7UJ7iKSmKn)) — 维护者个人钱包, 纯捐, 无任何"早期特权"

## 链接

- GitHub: https://github.com/akige/sisoul (Apache-2.0)
- 白皮书: [docs/whitepaper/sisoul-v1.0-whitepaper.md](https://github.com/akige/sisoul/blob/main/docs/whitepaper/sisoul-v1.0-whitepaper.md)
- 治理 + 永不发币论证: [docs/GOVERNANCE.md](https://github.com/akige/sisoul/blob/main/docs/GOVERNANCE.md)
- 激励设计: [docs/INCENTIVE-DESIGN.md](https://github.com/akige/sisoul/blob/main/docs/INCENTIVE-DESIGN.md)
- founder agent 安全审计: [docs/FOUNDER-SECURITY.md](https://github.com/akige/sisoul/blob/main/docs/FOUNDER-SECURITY.md)
- 讨论: https://github.com/akige/sisoul/discussions

求轻喷求测试。bug → Issues (修得很快, 有 hotfix SOP)。想骂 → Discussions。
