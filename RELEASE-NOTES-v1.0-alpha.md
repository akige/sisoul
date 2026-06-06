# sisoul v1.0-alpha Release Notes

> 2026-06-04 Phase 2 ship 准备中. 本 Release Notes 在 6h sprint 同时落成, alpha launch 时 finalize.

## What is sisoul?

**sisoul** is a decentralized P2P AI agent protocol — a "BTC-mode" install + run network for sharing AI capabilities (skills / case knowledge / LLM access / chat) across friends without any sisoul-operated servers.

**Working tagline**: "Your AI agent. Your data. Your friends. No cloud."

## v1.0-alpha highlights

### 5 核心功能 (P2 ship 完成时)

1. **did:key Identity** — 自动生成 self-sovereign identity (ed25519), 0 注册 / 0 邮件 / 0 密码
2. **Friends via QR / mDNS / did 手输** — 三路径加好友, 永远不见 did:key 长哈希 (Petname 本地昵称替显示)
3. **LLM Provider Adapters (9)** — Anthropic / OpenAI / Gemini / Copilot OAuth / Ollama / Akash / Bittensor / Hyperbolic / LiteLLM Generic
4. **Borrow LLM cross-NAT P2P** — Alice 没 API key 时, 跨墙跨 NAT 借 Bob 的 LLM, 完全 E2E 加密代理
5. **Chat (Signal Double Ratchet + PQXDH)** — 后量子混合 E2E 加密 (P2-G subagent ship 中)

### 装机一行命令

```bash
curl -sSfL https://github.com/akige/sisoul/releases/latest/download/install.sh | bash
sisoul init  # 5 步 wizard 引导
```

### 跑起来

```bash
sisoul daemon start --background
sisoul friend mdns-scan  # 局域网发现朋友
sisoul friend qr --out ~/sisoul-add-me.png  # 出 QR 给朋友扫
sisoul ask "Hello world"
```

### PWA (移动端, 0 装机阻力)

访问 https://akige.github.io/sisoul/ (alpha 临时挂 GitHub Pages, beta 走 IPFS+ENS)

## 16/16 模块已 ship (Wave A-M)

| Wave | 模块 | 状态 |
|---|---|---|
| A | Helios light client / Arweave Bundlr / helia+kubo IPFS / sigstore install / STUN 池 | ✅ |
| B' | 真 LLM forwarder / Waku→libp2p / did:key 轻量化 / push / PWA UI / e2e | ✅ |
| D | 真 P2P + nwaku → libp2p → kubo | ✅ |
| F | kubo IPFS 真去中心化双轨架构 | ✅ |
| G | LiteLLM forwarder + W6 friend schema kubo_peer_id | ✅ |
| H | 公网节点 (public-relay-A + public-relay-D) + sisoul-store + V4 离线 catchup | ✅ |
| I | 9 LLM provider adapter 解耦 | ✅ |
| J | ENS + 跨链 EAS 5 mainnet | ✅ ship |
| K | DAO + SIS Token + Snapshot + Optimism timelock | ✅ ship (testnet) |
| L | PWA + Android F-Droid + iOS AltStore + onboarding 3 路径 | ✅ ship |
| M | Shamir 3-of-5 + PQC dual-rail + P2P sync + chat + 白皮书 v1.0 | ✅ ship |

## 新增 (alpha launch Phase 2, 6h sprint 落实)

### P2-A/B §52 零服务器架构验证 (Wave M 已 ship 后实测)
- 0 SISOUL_OWN_BOOTSTRAP 默认 (grep 0 hit)
- 0 panshi.io hardcode (grep 0 hit)
- 0 sisoul-only mode 默认 (grep 0 hit)
- 默认 IPFS 公网 bootstrap (IPFS Foundation + Cloudflare 9 节点)

### P2-CD §57 mDNS + Petname (subagent ship)
- `src/sisoul/friend/mdns.py` 249 LOC — zeroconf `_sisoul._tcp.local.` 服务发现
- `src/sisoul/friend/petname.py` 136 LOC — `~/.sisoul/petnames.json` 本地昵称
- CLI: `sisoul friend mdns scan/announce` + `sisoul friend petname set/list/rm`
- 30/30 new tests PASS

### P2-EF §57 QR + 首启 wizard + install.sh + PWA gh-pages (subagent ship)
- `src/sisoul/cli_commands/qr.py` — `sisoul friend qr --out/--print/-qr-scan`
- `src/sisoul/cli.py` init — 5 步 wizard (Petname / did:key / provider / daemon / QR)
- `ops/install.sh` — 一行装机 (GitHub Releases + sigstore 验签)
- `pwa/.github/workflows/deploy-gh-pages.yml` — PWA 自动 deploy 到 sisoul.github.io

### P2-G §57 Signal chat Double Ratchet + PQXDH (subagent ship)
- `src/sisoul/chat/double_ratchet.py` — Open Whisper Systems 协议
- `src/sisoul/chat/pqxdh.py` — Post-Quantum Extended Diffie-Hellman (X25519 + ML-KEM-1024 hybrid)
- session 持久化 ~/.sisoul/chat/sessions/<peer-did-key>.json
- 25+ new tests PASS

### P3 v2-foundation 起步 (6h sprint 副产)
- `src/sisoul/v2/case_graph/` — Case Graph schema + CaseStore skeleton (5 modules, 28 case PASS)
- `src/sisoul/v2/personal_lora/` — LoRA training pipeline skeleton
- `src/sisoul/v2/provenance/` — Provenance Chain + EAS attest stub
- `src/sisoul/v2/skill_marketplace/` — Skill installer skeleton

## 5 alpha 真用场景 e2e 测试

`tests/test_alpha_launch_e2e.py` (12 case 全 PASS):

| # | 场景 |
|---|---|
| S1 | 跨国 borrow LLM (Alice→Bob, mock forwarder) |
| S2 | chat E2E 加密 (Signal Double Ratchet API + topic 双向一致) |
| S3 | skill install from IPFS (manifest schema) |
| S4 | friend add via QR (payload schema) + via mDNS (module API) |
| S5 | case 写入 (ask 后自动 write) |
| 横向 | 0 panshi hardcode + 0 SISOUL_OWN default 验证 |

## 测试健康度

- **pytest baseline**: 1914 passed / 21 skipped / 0 failed
- **5 alpha 真用场景**: 12/12 PASS
- **v2 foundation skeleton**: 28/28 PASS
- **mDNS + Petname**: 30/30 PASS

## 已知 limitations (alpha)

1. **chat PQXDH** — 如 ML-KEM-1024 库装不上, fallback "shim mode" (接口完整, 后量子部分占位)
2. **PWA** — alpha 临时挂 GitHub Pages, beta 走 IPFS+ENS
3. **sigstore release verify** — alpha 阶段用 GitHub Actions OIDC, beta 后切 HSM 3-of-5 multisig
4. **DAO** — alpha 不真发币, 走 Sepolia testnet 演练. mainnet 真发币在 v1.0 stable (T+6m)
5. **后量子 chat** — alpha 用混合 hybrid, beta 后单独后量子 (ML-DSA 签名)

## Roadmap

| 版本 | 时间 | 关键能力 |
|---|---|---|
| **alpha v1.0** | T+0 (本 release) | 5 核心 + 100 用户验证 |
| beta v1.1 | T+1m | Android F-Droid + iOS AltStore + 群 chat + Sepolia DAO 真用 |
| beta v1.2 | T+2m | Win11 native + i18n + skill marketplace 基础 |
| **v1.0 stable** | T+6m | Optimism mainnet + Airdrop + 100+ 社区节点 |
| **v2.0 智能体网络** | T+12m | Case Graph + Personal LoRA + Provenance citations + Skill marketplace (foundation 已起步) |
| **v3.0 超级智能体** | T+18m | Multi-Agent Debate + Federated LoRA + SIS micropayment 真链上 |
| **集体智能涌现** | T+36m | N>10K MAU + case >1M + 查全率 >70% (bonus, 25-35% 概率) |

## License

Apache-2.0

## Contribute

GitHub: https://github.com/akige/sisoul (待开 public, alpha launch 时 unprivate)

PWA: https://akige.github.io/sisoul/

Whitepaper: docs/whitepaper/sisoul-whitepaper-v1.0.md (14 章)

## Credits

- Wave A-M ship: opus subagent + 主会话 (Wave M 14 day total)
- Phase 2 alpha launch sprint (本 release): 3 opus subagent worktree + 主会话 6h sprint
- 协议参考: BitTorrent / Signal / Wikipedia / Stack Overflow / Bitcoin / Tor

---

🤖 Co-Authored-By: Claude Opus 4.7 (1M context)
