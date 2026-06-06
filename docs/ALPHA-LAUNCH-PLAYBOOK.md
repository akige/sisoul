# sisoul alpha v1.0 Launch Playbook

> 2026-06-04 创建. alpha launch ship 准备 + 真用户上手指南 + 5 真用场景 + 风险预案.

## 1. alpha v1.0 定位

- **what**: 去中心化 P2P AI agent 协议 - 第一个真正"装了就跑"版本 (BTC 模式, 0 sisoul 自营服务器)
- **who**: 早期 dev/极客 100-500 用户
- **how-to-install**: GitHub Releases → install.sh 一行装机
- **how-to-use**: sisoul init 5 步引导 → friend add (QR/mDNS) → ask/chat/borrow LLM

## 2. 装机一行命令

```bash
# alpha installer (release tarball + cosign + Pages install.sh 未上线, 走源码装)
git clone https://github.com/akige/sisoul && cd sisoul
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e '.[daemon,crypto,chat,llm]'
sisoul init  # 5 步 wizard 引导
sisoul daemon start --background
```

## 3. alpha 5 核心功能

| # | 功能 | CLI | 状态 |
|---|---|---|---|
| 1 | did:key 身份 | `sisoul init` 自动生 | ✅ Wave M ship |
| 2 | 朋友 (QR / mDNS / did 手输) | `sisoul friend add/qr/mdns-scan` | ✅ P2-CD/EF ship |
| 3 | LLM provider (BYO + skip + custom) | `sisoul provider set/list` | ✅ Wave I 9 adapter |
| 4 | borrow LLM (跨 NAT P2P) | `sisoul ask --via <friend>` | ✅ Wave F-I 真测 |
| 5 | chat (Signal Double Ratchet + PQXDH) | `sisoul chat send/recv` | 🔄 P2-G ship 中 |

## 4. 5 真用场景 e2e (tests/test_alpha_launch_e2e.py)

| # | 场景 | 验证点 |
|---|---|---|
| S1 | 跨国 borrow LLM (Alice→Bob) | borrow API + friend record schema v2 (kubo_peer_id) |
| S2 | chat E2E 加密 | Double Ratchet + PQXDH API + topic 双向一致 |
| S3 | skill install from IPFS | manifest schema (name/version/sigstore_sig/author_did) |
| S4 | friend add (QR + mDNS 两路径) | QR payload schema + mDNS module API |
| S5 | case 写入 (ask 后自动) | ask 模块 + case schema (id/question/answer/sources/did_author) |

横向: `test_alpha_zero_panshi_hardcode` + `test_alpha_zero_sisoul_own_bootstrap_default` 保证零服务器架构.

## 5. 首启 wizard 5 步

```
$ sisoul init
Step 1/5: Petname (你的昵称, 别人看到的名字): [hostname=alice-mbp] _
Step 2/5: 生成 did:key 身份... ✓ did:key:z6MkAlice...XYZ
Step 3/5: LLM provider (default-anthropic / custom / skip): default-anthropic
Step 4/5: daemon 模式 (background / foreground): background
Step 5/5: 出 QR 给朋友扫: [QR 显示]

✓ sisoul 装好了. 试试:
  sisoul friend add <从朋友那儿拿到的 did>
  sisoul ask "Hello world"
```

## 6. 风险预案

| 风险 | 缓解 |
|---|---|
| IPFS 公网 bootstrap 全挂 | 配置 4 个 (Foundation + Cloudflare + 2 个 DigitalOcean), 单点失败概率 < 1% |
| sigstore release 私钥泄露 | HSM 3-of-5 multisig + Rekor 公开透明日志 |
| 用户 NAT 完全对称 (STUN 失败) | Circuit Relay v2 fallback (kubo 自带) |
| 朋友圈太小 (N=1) | mDNS 局域网发现 + did 手输 + QR 互扫 3 路径冗余 |
| chat session state 丢失 | 持久化 ~/.sisoul/chat/sessions/ + 24h pre-key refresh |
| GitHub Releases 暴墙 | IPFS gateway mirror + Codeberg fallback |

## 7. launch 前 checklist (P2 完成时 verify)

- [ ] 1856+ pytest pass / 0 fail (basline)
- [ ] 12 alpha e2e pass
- [ ] mDNS module ship (P2-CD)
- [ ] Petname module ship (P2-CD)
- [ ] QR module ship (P2-EF)
- [ ] init wizard 5 步 (P2-EF)
- [ ] install.sh shellcheck clean (P2-EF)
- [ ] PWA gh-pages workflow ship (P2-EF)
- [ ] Signal chat Double Ratchet + PQXDH ship (P2-G)
- [ ] 3 worktree branch merge to main
- [ ] obs ship report §63 写完
- [ ] changelog 5 字段全写
- [ ] VERSION bump 到 1.0.0-alpha

## 8. launch 后第一周监控

- daily: GitHub Releases 下载量 / install.sh 跑成功率
- daily: PWA gh-pages 访问量
- weekly: 用户报告 issue 分类 (装机 / 体验 / bug)
- weekly: 朋友圈连接成功率 (cross-NAT)

## 9. 5 早期用户人选 (招募时优先)

- 极客 / 软件工程师 / 加密爱好者 (能容忍 alpha bug)
- 多设备用户 (Mac + Win + 手机 PWA, 测全链路)
- 国内 + 海外混合 (验证跨墙 P2P)
- 至少 2 人朋友圈互联 (互相 borrow LLM + chat)
- 愿意每周写 1 个 case (验证 case 复用闭环)

## 10. alpha → beta 过渡标准

- 100+ 用户实装 + 装机成功率 > 90%
- 0 critical bug 7+ day
- 5 alpha 场景全部用户调研 > 70% PASS

达到 → beta v1.1 加 native Android/iOS + 群 chat + Sepolia DAO.
