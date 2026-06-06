# sisoul alpha v1.0 Launch Announcement Drafts

> Draft templates for HN / Twitter / Reddit / Farcaster / Discord at launch. 修改 + post 时填实际数字.

## 1. Hacker News (Show HN)

### Title
```
Show HN: sisoul – decentralized P2P AI agent protocol (BTC-mode install)
```

### Body
```
sisoul is a peer-to-peer protocol for AI agents to share capabilities
(skills, case knowledge, LLM access, encrypted chat) across friends —
without any centralized server. Think BitTorrent + Signal + Wikipedia
applied to AI agents.

What you can do in alpha v1.0:

1. did:key Identity — self-sovereign, 0 signup / 0 email / 0 password
2. Friends via QR / mDNS / DID input — 3 ways to add, Petname display
3. 9 LLM Provider Adapters — Anthropic, OpenAI, Gemini, Copilot, Ollama,
   Akash, Bittensor, Hyperbolic, LiteLLM Generic
4. Borrow LLM cross-NAT P2P — friend's API key, end-to-end encrypted
5. Signal-grade Chat — Double Ratchet + PQXDH (post-quantum hybrid)

Install (one line):
$ curl -sSfL https://github.com/akige/sisoul/releases/latest/download/install.sh | bash

Then: sisoul init  (5-step wizard)

PWA (no install): https://akige.github.io/sisoul/

Architecture: kubo IPFS embedded + libp2p GossipSub + sigstore signing +
EAS attestation on Optimism L2. 16 modules total. 2013 pytest pass.

What's NOT in alpha:
- Single sign-on with email (intentionally — your did:key is your identity)
- Multi-agent debate (v3.0, ~18 months)
- Mainnet DAO token (v1.0 stable, ~6 months; SIS will be airdropped)

Built by 主会话 + 3 opus subagent worktree pairs in a 6h sprint.

Apache-2.0 licensed. Whitepaper: 14 chapters, in repo.

Looking for 100 alpha users + critique. Esp. on:
- Cross-NAT friend-to-friend LLM borrow reliability
- Petname / mDNS UX
- Skill marketplace design (foundation in v2.0, ~12 months)

GitHub: https://github.com/akige/sisoul

Happy to answer questions.
```

## 2. Twitter / X (thread)

### Tweet 1
```
🚀 just shipped sisoul alpha v1.0

decentralized P2P AI agent protocol. BTC-mode install + run. no servers operated by us.

your AI agent. your data. your friends. no cloud.

curl -sSfL .../install.sh | bash
sisoul init

🧵 ↓
```

### Tweet 2
```
core in alpha:

1. did:key identity (self-sovereign, 0 signup)
2. friends via QR / mDNS / DID (Petname display)
3. 9 LLM provider adapters (Anthropic/OpenAI/Gemini/Copilot/Akash/...)
4. borrow LLM cross-NAT P2P (E2E encrypted)
5. Signal-grade chat (Double Ratchet + PQXDH post-quantum hybrid)
```

### Tweet 3
```
stack:
- kubo IPFS embedded
- libp2p GossipSub for P2P
- sigstore keyless OIDC signing
- EAS attestation on Optimism L2
- SIS token (not minted yet, mainnet T+6m)

2013 pytest pass / 0 fail in 6h sprint.
```

### Tweet 4 (call to action)
```
looking for 100 alpha users:

- early adopter / hacker / crypto / privacy people
- multi-device folks
- ok with rough edges

GitHub: github.com/akige/sisoul
PWA: akige.github.io/sisoul
docs (14-ch whitepaper): docs/whitepaper

Apache-2.0 / no VC / no token sale (yet)
```

### Tweet 5 (roadmap)
```
roadmap:

alpha v1.0 = now (5 core + Signal chat)
beta T+1m = Android F-Droid + iOS AltStore
v1.0 stable T+6m = Optimism mainnet + SIS airdrop
v2.0 T+12m = Case retrieval + Personal LoRA + Provenance
v3.0 T+18m = Multi-Agent Debate + Federated LoRA
涌现 T+36m = collective intelligence (25-35% probability, bonus)
```

## 3. Reddit r/selfhosted / r/decentralization

### Title
```
sisoul alpha v1.0 — decentralized P2P AI agent protocol (Apache-2.0, BTC-mode install)
```

### Body (same structure as HN but emphasize self-hosting angle):
```
Hi r/selfhosted,

I just released alpha of sisoul, a P2P protocol for AI agents to share
capabilities (skills, case knowledge, LLM access, encrypted chat) across
friends without ANY sisoul-operated server.

For folks who want to self-host an "agent fabric" + connect to friends'
agents without cloud lock-in.

Five core in alpha:
1. did:key Identity (no email/password)
2. Friends via QR / mDNS / DID (LAN discovery)
3. 9 LLM provider adapters (BYO API key OR borrow friend's)
4. Borrow LLM cross-NAT P2P (E2E encrypted)
5. Signal-grade Chat (post-quantum hybrid)

Tech: kubo IPFS + libp2p + sigstore + EAS + Apache-2.0

Install:
$ curl -sSfL .../install.sh | bash
$ sisoul init  # 5-step wizard

Documentation: 14-chapter whitepaper in repo

Looking for self-hosters + sysadmin types to dogfood.

Repo: github.com/akige/sisoul
```

## 4. Farcaster (web3 friendly)

### Cast 1
```
just shipped sisoul alpha 🤖🌐

decentralized P2P AI agent protocol.
- did:key 身份 / 朋友 / 9 LLM providers
- borrow LLM cross-NAT
- Signal chat + post-quantum

Optimism EAS attestation. SIS token T+6m airdrop.
no servers we operate.

github.com/akige/sisoul
```

## 5. Discord (long-form, e.g. ETHGlobal)

```
Hey everyone — just shipped sisoul alpha v1.0.

**TL;DR**: decentralized P2P AI agent protocol. Install + run, no servers
operated by sisoul team.

**Stack**:
- did:key identity (self-sovereign)
- kubo IPFS + libp2p GossipSub (P2P)
- 9 LLM provider adapters (BYO key)
- Signal Double Ratchet + PQXDH (chat)
- EAS attestation on Optimism L2 (provenance)
- sigstore keyless OIDC (release signing)
- SIS token + DAO (mainnet T+6m)

**What's NEW in alpha** (post Wave A-M):
- mDNS local friend discovery
- Petname local nicknames (never see did:key z6Mk... hashes)
- QR code add-friend (cli + PWA)
- 5-step init wizard
- install.sh one-line release (sigstore verified)
- PWA on akige.github.io/sisoul/

**Looking for**:
- alpha users (100 target)
- skill developers (P2P MCP marketplace v2.0)
- DAO contributors (Sepolia testnet → mainnet T+6m)
- whitepaper reviewers (14 chapters in repo)

**Roadmap**:
- alpha v1.0: NOW
- beta v1.1: T+1m (Android/iOS native)
- v1.0 stable: T+6m (mainnet + airdrop)
- v2.0 智能体网络: T+12m (case retrieval + LoRA + provenance)
- v3.0 超级智能体: T+18m (debate + federated)
- 集体智能涌现: T+36m (10K+ MAU bonus, 25-35% probability)

**Repo**: https://github.com/akige/sisoul
**PWA**: https://akige.github.io/sisoul/
**Whitepaper**: docs/whitepaper/sisoul-whitepaper-v1.0.md

Built in a 6h sprint (主会话 + 3 opus subagent worktree pairs).
Apache-2.0, no VC, no token sale yet.

Happy to AMA.
```

## 6. 中文社区 / V2EX / 即刻 / 知乎

### 标题
```
[开源] sisoul - 去中心化 P2P AI agent 协议 (alpha v1.0, Apache-2.0)
```

### 正文
```
做了一个去中心化 P2P AI agent 协议, alpha v1.0 刚发布.

定位 BTC 模式 - 装了就跑, 没有 sisoul 团队运营的任何服务器.

**5 核心功能** (alpha 已 ship):

1. **did:key 身份** - 自动生成自我主权身份, 0 注册 / 0 邮件 / 0 密码
2. **加朋友 3 路径** - QR / mDNS 局域网 / did 手输, 永远不见 z6Mk... 长哈希 (Petname 本地昵称替显)
3. **9 LLM provider** - Anthropic/OpenAI/Gemini/Copilot/Ollama/Akash/Bittensor/Hyperbolic/LiteLLM
4. **借朋友 LLM 跨墙跨 NAT** - 你没 API key 没关系, 朋友给你借, 端到端加密
5. **Signal 级 chat** - Double Ratchet + PQXDH 后量子混合

**技术栈**:
- kubo IPFS 内嵌
- libp2p GossipSub (跨墙 NAT P2P)
- sigstore keyless 签名
- EAS attest on Optimism L2
- Apache-2.0 / Whitepaper 14 章

**装机**:
```
curl -sSfL https://github.com/akige/sisoul/releases/latest/download/install.sh | bash
sisoul init  # 5 步引导
```

**PWA (0 装机)**: https://akige.github.io/sisoul/

**Roadmap** (alpha → 涌现 36 month):

- alpha v1.0 (现在): 5 核心 + 100 用户
- beta T+1m: Android + iOS
- v1.0 stable T+6m: Optimism 主网 + SIS 空投
- v2.0 T+12m: 朋友圈 case 共享 + 个人 LoRA
- v3.0 T+18m: Multi-Agent Debate + Federated LoRA
- 涌现 T+36m: 10K+ MAU + 集体智能 (bonus, 25-35% 概率)

招 100 个 alpha 用户, 早期参与可以拿 SIS 空投 (T+6m mainnet 时).

GitHub: github.com/akige/sisoul
```

---

各渠道 launch checklist:
- [ ] HN: submit Tuesday/Wednesday 8-10am PT
- [ ] Twitter: thread with media (架构图)
- [ ] Reddit: r/selfhosted + r/decentralization + r/MachineLearning (small subreddits first)
- [ ] Farcaster: warpcast feed with /sisoul channel
- [ ] Discord: ETHGlobal / Optimism / IPFS / Signal communities
- [ ] V2EX / 即刻 / 知乎: 中文社区
- [ ] Mastodon: fosstodon.org
- [ ] Lobste.rs: invitation required

monitor signals:
- daily install.sh download
- PWA gh-pages visit
- GitHub stars + issues
- discord/Farcaster mentions
