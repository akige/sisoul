## V2EX 发布文 (final · 含真命令清单 · alpha 真路径 · 含 USDT 捐助)

> 标题: `[分享] sisoul · 自己 own 的 AI agent · 永不发币 · 招 alpha 极客一起测`

---

最近做了个开源项目 sisoul, 7 个月闭关写完, 招几个极客一起测 alpha. 仓还 0 star, 你来就是第一波.

## 你今天来能跑什么命令 (真测过, 100% 诚实)

**单机闭环 (一个人就能玩):**

```bash
sisoul init --goals "..."                              # 建 vault + 12 词 BIP-39
sisoul founder init --from vault-template/founder      # 装 @founder agent
sisoul founder chat "为什么 sisoul 不发币?"            # 真引用 vault case 答你
sisoul backup                                           # 打包 vault → zip
sisoul restore <12 words>                              # 跨机恢复
sisoul self-check                                       # 8/8 验证
sisoul daemon &                                         # 起 daemon :9876
sisoul status                                           # 看 vault / daemon 状态
python ops/scripts/rsi-daily-suggest.py                # 让 LLM 改进 @founder prompt
```

**双机/朋友 (找一个朋友一起装两台):**

```bash
sisoul friend qr                                        # 生 QR 给朋友扫
sisoul friend qr-scan <png>                            # 扫朋友 QR
sisoul friend add did:key:z6Mk...                       # 直接粘 did:key 加好友
sisoul friend mdns                                      # 局域网 Bonjour 发现
sisoul friend petname <did> alice                       # 起本地昵称
sisoul borrow run FRIEND_DID skill_llm 5000 -p "..."   # 借朋友 LLM quota (今天默认 gift 模式: 借出方免费)
sisoul lend list / approve / deny                      # 借出方收到 request → 批准/拒绝
sisoul chat send <did> "hi"                            # Signal Double Ratchet + PQXDH 端对端聊
sisoul chat recv                                        # 收消息
```

**3 档 incentive 全 MVP 已 ship (2026-06-06 当晚补完)**:

- ✅ **gift 模式** (借出方免费): `sisoul borrow run DID llm_quota 5000` 默认.
- ✅ **kudos 模式** (非货币计数): `sisoul kudos balance/grant/history/decay` 全 CLI 真跑, `sisoul borrow run --dry-run` 真显借 5000 token 扣几个 kudos. 5%/月衰减 daily LaunchAgent 真挂.
- ✅ **USDT-TRC20 micropay 模式** (真钱): `sisoul wallet set-usdt-trc20 T...` 设你的收款地址, `sisoul borrow run --dry-run` 真显借出方要收多少 USDT + tronscan 验证 link + "付了 tx hash 给借出方" 人话指示.
- 25/25 pytests 真过 (18 incentive + 7 v1 integration).

**今天还没实现的 (诚实分级)**:

- ✅ **链上 mainnet attestation** — Optimism mainnet EAS 真注册. 第一个 username `@akige` 已上链 ([tx 0xabcb1bab...](https://optimistic.etherscan.io/tx/0xabcb1bab93946d491503a6e1368ee8c6b870085e185eed83f629459d865bb72c) · [easscan attestation](https://optimism.easscan.org/attestation/view/0x78375e7ed6cbec575f630be8e32377da91de4801e6f9799bfb16a7c71ca6acdb)). 任何人 `sisoul username resolve akige` 立刻通过 easscan GraphQL 拿到 did:key, 整链路 0 中心化 server. 你的 username 注册 = 你自己签 + 你自己付 ~$0.5-1 OP ETH gas, sisoul 完全不沾钱也不沾权.
- ✅ **USDT 自动到账确认** — alpha v1.1 已 ship. `sisoul lend auto-approve enable` 后, 借出方 daemon 每 30s 轮询 TronGrid, USDT 到账自动 approve + GossipSub 发 ACK 给借入方. 借出方 0 手工干预.
- ✅ **A3 借用走 GossipSub** — 借用请求不再走 Waku push, 改走 IPFS kubo GossipSub per-DID topic (`/sisoul/lend/v1/<sha256(did):16>`). 完全去中心化, aws/cloud 主机 host_policy 物理拒跑 GossipSub.
- ✅ **install.sh 一行装** — `curl -sSL https://raw.githubusercontent.com/akige/sisoul/main/install.sh | bash` 真测过, 自动探测 OS + Python + kubo + 写 wrapper + PATH. mac 还可 `brew install --formula https://.../Formula/sisoul.rb`.
- ✅ **macOS 菜单栏 native app** — `tools/menubar/` 已 ship. `cd tools/menubar && bash build_app.sh` 真打出 29MB `Sisoul.app` (rumps + py2app), Finder 双击就跑. menu bar 显示 `S•` (online) / `S` (offline) + 15 个菜单项 (Add friend / Ask founder / Borrow LLM / Start-Stop daemon / Open dashboard ...). 真测过 `Add friend...` 弹 native dialog 真发 subprocess 调 sisoul CLI, EAS Optimism resolve 真打到 mainnet GraphQL.
- ✅ **PWA dashboard 上线** — [akige.github.io/sisoul/](https://akige.github.io/sisoul/) 浏览器打开就看. daemon offline 时显示 5 步装机命令 (带复制按钮) + 3 核心场景卡片 + GitHub/INSTALL 底链; daemon online 切到 Vault/Friends/Lend/Borrow/Chat 全功能 UI.
- ❌ **iOS / Android native app** — Skeleton 在 `mobile/{ios,android}/` (Swift Package + Kotlin Gradle), 单元测试通过但**没用户能下载的 .ipa / .apk**. Roadmap T+1m~T+2m.

## 3 个核心场景

- 你在自己机器上 `sisoul founder chat "为什么不发币"` — 它真引用代码库里 sprint-8-zh-no-token 这个 case 答你, 不瞎编. 这个 founder 是个 LLM persona, 装在你的 vault 里, 今天用 Claude 跑, 明天换 GPT 也是同一个它.
- 朋友手机没钱开 GPT-Plus, 你把自家 API key 借给他 — prompt 经过 PQXDH + Double Ratchet 加密, 你看不到他在问啥, 他用不了你 key 本体. **今天走 gift 模式 (借出方免费, 双方 reputation 互惠)**. 想强制陌生人付费的 kudos / USDT micropay 是下一步.
- 你卸了我们也不知道 — 没服务器、没数据库、没你的邮箱手机号. vault 在你硬盘, 12 个 BIP-39 助记词在你脑子里.

## 3 个核心原则 (硬约束)

- **永不发币** (白皮书 §4.10). Tor 22 年没 token, Mozilla 25 年, IETF 40 年, Linux 33 年. 基础设施跑得长就是因为没 token. `docs/GOVERNANCE.md` 全文公开 funding 模型. **不接受请绕道**.
- **永不下架** (§4.11). 我家断电协议还跑.
- **没空投、没积分**. 早期 alpha 拿个不可转 Soulbound Badge 留念 (0 经济价值, v1.0 stable T+6m 发).

## 装机 (Python 3.11+, macOS/Linux/WSL2 都验证过, 完整步骤 docs/INSTALL.md)

```bash
# 推荐: 一行装 (auto 探测 OS+Python+kubo, no sudo)
curl -sSL https://raw.githubusercontent.com/akige/sisoul/main/install.sh | bash

# Mac 用户还能 brew 装
brew install --formula https://raw.githubusercontent.com/akige/sisoul/main/Formula/sisoul.rb

# 或老派 4 步源码装 (要 Python 3.11+, macOS 默认 3.9 不行)
git clone https://github.com/akige/sisoul
cd sisoul
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e '.[daemon,crypto,chat,llm]'

# 2. 装 wrapper 让 sisoul 命令全局可用 (不用每次 activate venv)
mkdir -p ~/.local/bin && cat > ~/.local/bin/sisoul <<EOF
#!/usr/bin/env bash
exec $PWD/.venv/bin/sisoul "\$@"
EOF
chmod +x ~/.local/bin/sisoul
echo 'export PATH=$HOME/.local/bin:$PATH' >> ~/.zshrc && exec $SHELL

# 3-7: 见上 "你今天来能跑什么命令" 段
```

## 借 LLM 的激励/成本设计 (docs/INCENTIVE-DESIGN.md 全文 · 3 档)

| 场景 | 模式 | 借出方拿 | 借入方付 | 今天 ship 了? |
|---|---|---|---|---|
| 真朋友 | gift | reputation +20 + 社交感谢 | 0 | ✅ 默认就是 |
| V2EX 网友 | kudos | 计数 +N | 计数 -N (不可转, 5%/月衰减) | ✅ MVP 已 ship |
| 陌生人/紧急 | USDT-TRC20 micropay | 直接收 USDT 到自己钱包 | 0.01 USDT/1k token + TRX gas | ✅ MVP 已 ship (dry-run quote 真测过) |

**sisoul 在 USDT micropay 里抽 0% — 借入方直接打钱给借出方, 我们不托管不路由. 守 §4.10**.

reputation grade A/B/C/D 是信号层 (上链 EAS), 不阻断借 — 你可以借给 F 级, 你可以拒绝 A 级. 是参考, 不是门禁.

## 支持开发 (我们靠 grants + sponsorship + donations, 不发币)

- **USDT (TRC20)**: `TNesE1mJZ11ogsrPC7tsG2he7UJ7iKSmKn` ([tronscan 链上可查](https://tronscan.org/#/address/TNesE1mJZ11ogsrPC7tsG2he7UJ7iKSmKn))
- 这是维护者自己的钱包 (跟 panshi.io 同一个), 不是新发的, 不是 token, 没有"早期捐助者特权". 纯捐, 透明上链.
- 其他: Optimism RetroPGF (T+6m 申请) / Gitcoin / EF Grant. 完整模型 `docs/GOVERNANCE.md`.

## 链接

- GitHub: https://github.com/akige/sisoul (Apache-2.0)
- 装机指南 (4 步真装): [docs/INSTALL.md](https://github.com/akige/sisoul/blob/main/docs/INSTALL.md)
- 诚实状态盘点 (Q1-Q8 真状态): [docs/ALPHA-LAUNCH-STATUS-2026-06-06.md](https://github.com/akige/sisoul/blob/main/docs/ALPHA-LAUNCH-STATUS-2026-06-06.md)
- 激励设计 (gift/kudos/micropay): [docs/INCENTIVE-DESIGN.md](https://github.com/akige/sisoul/blob/main/docs/INCENTIVE-DESIGN.md)
- 治理 + 永不发币论证: [docs/GOVERNANCE.md](https://github.com/akige/sisoul/blob/main/docs/GOVERNANCE.md)
- 白皮书 §4.10: [docs/whitepaper/sisoul-v1.0-whitepaper.md](https://github.com/akige/sisoul/blob/main/docs/whitepaper/sisoul-v1.0-whitepaper.md)
- founder agent 安全审计 (能不能控制你电脑): [docs/FOUNDER-SECURITY.md](https://github.com/akige/sisoul/blob/main/docs/FOUNDER-SECURITY.md)
- 讨论: https://github.com/akige/sisoul/discussions

求轻喷求测试. bug → Issues. 想骂 → Discussions.
