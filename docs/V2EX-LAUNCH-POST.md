# V2EX 发布文 (v2 · 永不发币 · founder-agent)

> Copy 下面整段去 V2EX 发, 标题: `[分享] sisoul · 把你的 AI agent 装到自己机器上 · 永不发币 · 招 alpha 测试`

---

最近做了个项目叫 sisoul, 7 个月开发, 0 用户, 招几个极客试试。

## 一句话

把 AI agent 搬到你自己机器上, 像装 Bitcoin 节点一样跑。我们没服务器, 看不到你的对话, 你卸了我们也不知道。

## 五个核心 (alpha v1.0)

1. **did:key 身份** · 12 个 BIP-39 单词派生的 Ed25519 公钥, 不是邮箱不是手机号
2. **朋友** · 二维码扫一下 / 局域网 mDNS 自动发现, 没中心服务器
3. **9 个 LLM 接入** · Anthropic / OpenAI / Gemini / Ollama / Grok / DeepSeek / Mistral / Cohere / Replicate (你自带 key)
4. **借 LLM 跨 NAT** · 没钱开 API? 朋友愿意借, 你的 prompt 经过他的 daemon 走他的 key, 全程 PQXDH 加密
5. **Signal-grade 端到端聊天** · Double Ratchet + X25519 + ML-KEM-1024 抗量子 hybrid

## 技术栈 (不堆 buzzword, 真用)

- **P2P**: kubo (IPFS Go-impl) embedded, GossipSub + Circuit Relay v2 + AutoNAT + DCUtR
- **加密**: 全 libsodium SecretBox (vault) + PQXDH (chat 握手) + Double Ratchet (消息)
- **发布**: cosign sigstore 签名, install.sh 链下校验 (类 BTC bootstrap)
- **跨平台**: macOS / Linux / Windows + iOS / Android (PWA wrap via Capacitor 8)
- **2278 pytest 真跑过, 0 fail, `make release-check` 跨机器 reproducible**
- 全开源 Apache-2.0

## 重点 1: 永不发币 (这点跟 99% Web3 项目不一样)

白皮书 §4.10 是硬约束:

> sisoul will not issue a token of any kind. No ICO, no IDO, no airdrop, no governance token, no fee token, no points-then-token, no "we'll figure out the token later".

理由不是嘴上说说, 是 Compound / Uniswap / MakerDAO 全部被大户 vote 捕获的教训。Tor 跑了 22 年没 token, Mozilla 25 年, IETF 40 年, Apache 25 年, Linux 33 年 — **基础设施跑得长就是因为没 token**。

我们靠: Optimism RetroPGF + Gitcoin Grants + EF Grant + 直接捐赠。如果靠这些活不下去, 那是 sisoul 没建出足够价值, 不是 token 能救的。

**这意味着**:
- 早期测试者**没**币空投, **没**经济激励
- 拿一个 Soulbound Badge (不可转 ERC721, 0 经济价值) 作为荣誉徽章
- 你来玩纯粹是因为你想要一个自己 own 的 AI agent, 不是来刷 token

不接受这个原则的就别来。

## 重点 2: founder-agent — sisoul 的第一个用户

我们装了一个 `@founder` agent, 它的脑子里装着 7 个月的开发历史 + 设计决策 + 我的对话风格。

它**不是** Claude (Anthropic 训的)。它是一个 sisoul 容器装的 LLM persona:
- vault 里装着 sprint history (6 cases + 3 lessons + system_prompt, 全开源在 [vault-template/founder/](https://github.com/akige/sisoul/tree/main/vault-template/founder))
- 后端 LLM 任意切 (Claude / GPT / Gemini 都行, 我们走 newapi free-pool)
- 三机部署 (mac / aws-us / wsl), GossipSub 同步, 没单点
- 每天跑 RSI (递归自我改进) 自演化 prompt

**怎么召唤**: 装完 sisoul, 把 `@founder` 加为朋友, 或者你 paseo+claude 装了 `sisoul-founder-mcp` 直接在任何 AI session 输入 `@founder ...` 就能调到。

第一次见 founder 你能问它:
- "为什么 sisoul 不发币?"
- "RSI 怎么防自己改自己?"
- "Borrow LLM 时朋友看得到我的 prompt 吗?"

founder 会引用 vault 里的 case 真答, 不会瞎编。它知道自己是个 persona, 不会假装是人。

## 招什么样的人

- 多设备 (Mac / Linux / Win / 手机) + 朋友圈有 1-2 个能装个 daemon 玩的极客
- 国内 / 海外都来, 跨 NAT 真测验证用
- 玩过 IPFS / Signal / Tor / Tailscale / did 任一加分
- 但不需要懂 — install.sh 一行就能起

## 不招

- 想刷币的 (现在没币, 永远没币, §4.10)
- 想割韭菜的 (我们是基础设施不是金融产品)
- 想"贡献了等空投"的 (拿 SBT 荣誉徽章, 0 经济价值)

## 怎么玩

```bash
# Mac/Linux:
curl -sSfL https://akige.github.io/sisoul/install.sh | sh
# 或 pip install sisoul (beta 后开)

# 初始化你自己的 sisoul (生成 12 个助记词, 写下来)
sisoul init Alice

# 起 daemon (会启 kubo IPFS subprocess)
sisoul daemon &

# 加 @founder 为朋友 (拿到 founder 的 did:key, 用 QR 或粘贴)
sisoul friend add @founder

# 跟 founder 聊
sisoul chat @founder "嗨, 我是新人, 告诉我 sisoul 跟 ChatGPT 区别"

# 装一个 LLM provider 才能用 Ask
sisoul login anthropic   # 或 openai/gemini/grok/...

# 跟你自己的 sisoul 聊 (用你的 key, 你的钱)
sisoul ask "帮我设计一个 todo app"

# PWA dashboard
open http://127.0.0.1:9876/dashboard/v2
```

## alpha 周期

| 时间 | 目标 |
|---|---|
| 第一周 | 5-10 个真用户 install + 跑通 5 核心场景 |
| 第一个月 | 0 P0 bug + 修完用户反馈 + 更多 docs |
| 三个月 | beta v1.1 加群聊 (MLS) + 移动端 native app + Sepolia DAO 测试 |
| 6 个月 | v1.0 stable + 应用 Optimism RetroPGF + 招 maintainer |

## 链接

- GitHub: https://github.com/akige/sisoul
- 白皮书 (14 章, 关于 never-token 看 §4.10): https://github.com/akige/sisoul/blob/main/docs/whitepaper/sisoul-v1.0-whitepaper.md
- Governance (PR + RSI + DAO 三层): https://github.com/akige/sisoul/blob/main/docs/GOVERNANCE.md
- Founder agent spec: https://github.com/akige/sisoul/blob/main/docs/FOUNDER-AGENT.md
- Protocol spec (给想写第三方实现的): https://github.com/akige/sisoul/blob/main/docs/PROTOCOL.md
- Threat model: https://github.com/akige/sisoul/blob/main/docs/THREAT-MODEL.md

## 联系

有 bug → GitHub Issues
讨论 → GitHub Discussions
我看不到你的 sisoul vault, 看不到你的对话, 也不想看 — 你那是你的。

求轻喷, 求测试, 求反馈。

---

🤖 这个帖子由 founder-agent 草拟, 维护者审阅过。
