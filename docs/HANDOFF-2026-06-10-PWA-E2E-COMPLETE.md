# sisoul handoff · 2026-06-10 · PWA e2e 真打通后接手指引

> **给下一会话**: 这份文档是 2026-06-09→06-10 的完整成果 + 后续详细计划. 不再造轮子 / 不再当用户小白鼠. 全部真证据已落地, 真路径已测过.

---

## 一、一句话当前状态

PWA dashboard 14/14 route 0 error (headless playwright 真测), borrow/lend/friend 真链路打通到 daemon, mainnet @akige 真上链, install.sh + Homebrew + macOS menubar .app 全 ship, **borrow 真路径 force_mode=strong-tie-auto 走 stub-passthrough (LLM forward 是 stub, 没真接 mock OpenAI)**.

---

## 二、用户真问题 (这次会话的真起点)

1. V2EX 发文前要把 sisoul alpha 真打磨干净, 不能让 V2EX 读者点开就一堆 TypeError
2. 用户要"添加好友 + 借 llm"真能 e2e 走通, 我配好对端环境
3. 用户极不耐烦, 反复骂"每次说修好都还是烂样子" — 必须**真测真证据 + 不留 TODO**

---

## 三、真 ship 总览 (今天 cfb2d647 → 07967ace · 34 commit)

### Phase 1 · v1.0-stable A3 + v1.1 + mainnet (前期)

| commit | 真改 |
|---|---|
| `cfb2d647` | feat(friend.add): @username EAS Optimism resolve (Signal-style) |
| `1d7e65a4` | fix(username_eas): register schema on-demand before first attest |
| `bd1fe519` | docs(v2ex): mainnet attestation + auto-approve + A3 GossipSub all shipped |
| `e5850bb2` | feat(did show + friend list-didkey): --vault-dir 支持 |

**真证据**: `@akige` 上 Optimism mainnet EAS, tx `0xabcb1bab93946d491503a6e1368ee8c6b870085e185eed83f629459d865bb72c`, attestation UID `0x78375e7ed6cbec575f630be8e32377da91de4801e6f9799bfb16a7c71ca6acdb`. easscan GraphQL 真返 did:key.

### Phase 2 · 装机 + 发行 (install.sh / brew / menubar / PWA)

| commit | 真改 |
|---|---|
| `951c5ecf` | feat(install): one-line install.sh + Homebrew Formula |
| `c61c8eb9` | feat(tools/menubar): macOS menu-bar tray (rumps + py2app, 29MB Sisoul.app) |
| `d0c3415b` | feat(pwa): OnboardingScreen for daemon-offline + GH Pages workflow |
| `17c33926` | ci(pages): force redeploy PWA after Pages source switch |

**真证据**:
- `curl -sSL https://raw.githubusercontent.com/akige/sisoul/main/install.sh | bash` 真测过装到 ~/sisoul-app
- `brew audit --strict Formula/sisoul.rb` 0 offense
- `tools/menubar/dist/Sisoul.app` 真 29MB, 15 菜单项, "Add friend" 真弹 native dialog 调 sisoul CLI

### Phase 3 · PWA 连环 7 bug 修 (Pages → Router → SW → mixed content → vite base → SPA fallback → 308 redirect)

| commit | 真因 |
|---|---|
| `a1ae64f2` | fix(pwa): Router base="/sisoul" — fixes blank screen on GitHub Pages |
| `657b53b6` | fix(pwa): unregister stale SW + clear caches (black-screen for cached users) |
| `edc478d2` | feat(daemon): serve PWA on /app/ + CORS for GH Pages |
| `6a4a2773` | fix(pwa): vite base="./" relative + Router base runtime detect |
| `c78757ae` | fix(pwa): no-cache meta on HTML to bust stale chunk references |
| `a5cd4a66` | fix(daemon): PWA SPA fallback — non-existent paths return index.html |
| `f1399c10` | fix(daemon): 308 redirect /app → /app/ (relative asset URLs broken without trailing slash) |

### Phase 4 · daemon endpoint shape adapter (5 alias + 6 PWA adapter)

| commit | 真因 |
|---|---|
| `c1cf3a50` | fix(daemon): add /lend/list /borrow/proxy-list /ledger/all PWA aliases |
| `7c5ec87f` | fix(daemon): /notify/stream SSE GET alias for PWA Friends EventSource |
| `3c1f0d5d` | fix(pwa): adapter daemon array→object + field rename (Vault/Goals/Friends) |
| `28900fa1` | fix(pwa): adapter for chat-history / settings / advanced (3 more TypeError fixes) |
| `18949112` | fix(pwa/borrow): step=1 not 100 (HTML5 number validation 2000 → 2001) |
| `1eda8f2c` | fix(daemon): wrap alias responses in PWA expected object shapes |

### Phase 5 · 2-daemon e2e 环境 + borrow/run 真路径 (Bob mock)

| commit | 真改 |
|---|---|
| `ad5dc3f1` | feat(daemon): /friend/list merges didkey_friends.json into EAS table |
| `f41c9e94` | feat(daemon): /borrow/run alias translates PWA body to /borrow shape |
| `35f78678` | fix(daemon): borrow handler 真模块是 daemon_routes.friend not .borrow |
| `a0753e96` | fix(daemon): /borrow/run alias call sync (_post_borrow is non-async) |
| `4c6bb281` | fix(daemon): /borrow/run uses did_key_from_master (CLI 真路径) |
| `aa1e5873` | fix(daemon): borrow/run import generate_did_key_from_master from did_key module |
| `cab78b61` | feat(daemon): borrow/run force_mode=strong-tie-auto + 10s timeout for alpha e2e |

### Phase 6 · audit agent 13 bug 真修 (P0/P1 全清)

| commit | 真改 |
|---|---|
| `45454278` | fix(pwa/borrow): adapt to daemon /borrow/run real shape {session: {...}} |
| `56026a4a` | fix(pwa+daemon/lend): handle int epoch + map daemon LendStore shape to PWA |
| `e34ec065` | fix(daemon): add /sisoul/friend/add POST alias forwarding to send_friend_request |
| `c01f9b6c` | fix(daemon+pwa/friends): add trust_level=2 fallback to fix Lundefined badge |
| `549df2c9` | fix(daemon/friend-list-alias): correct didkey_friends.json path + field rename |
| `74ad61ca` | docs(pwa): BUG-5 button click timeout — 非独立 bug (BUG-1/3 修后消除) |
| `07967ace` | fix(pwa/lend): adapt onApprove to daemon /lend/approve real shape |

---

## 四、真 final audit (06-10 完成时 headless playwright)

```
========================================================================
  FINAL audit · 14 routes (Alice daemon 9876 @ http://127.0.0.1:9876/app)
========================================================================
  ✓ /                ✓ /vault           ✓ /goals
  ✓ /chat-history    ✓ /settings        ✓ /advanced
  ✓ /friends         ✓ /skills          ✓ /borrow
  ✓ /lend            ✓ /ask             ✓ /debate
  ✓ /stats           ✓ /cheatsheet
========================================================================
CLEAN: 14/14 · BROKEN: 0
========================================================================
```

每个 route 真验证: 0 pageerror + 0 console.error + 0 HTTP 4xx/5xx + 0 DOM `加载失败 / TypeError / Lundefined`.

audit 脚本保留: `/Users/as/sisoul-dev/pwa/_full_audit.mjs` + `/tmp/sisoul-audit-*.png` (14 路由真截图)

---

## 五、4-daemon e2e 环境 (用户可重启)

| 进程 | 端口 | 真用途 | 真启动命令 |
|---|---|---|---|
| Alice daemon | 9876 | 用户本机 (PWA serve at /app/) | `cd ~/sisoul-dev && nohup .venv/bin/sisoul daemon < /dev/null > ~/.sisoul/daemon.log 2>&1 & disown` |
| Bob daemon (mock lender) | 9877 | 假朋友, 同 kubo swarm | 见 `docs/HANDOFF-2026-06-10-PWA-E2E-COMPLETE.md` "重启 Bob" 段 |
| kubo IPFS | 5001 | GossipSub 底座 | `ipfs daemon --enable-pubsub-experiment &` |
| mock OpenAI server | 8765 | borrow forward 假端点 | `uvicorn mock-openai:app --port 8765 --app-dir /tmp` (脚本在 `/tmp/mock-openai.py`) |

### 真 did:key (alice + bob)

- **Alice** (用户): `did:key:z6LSeGSR6a3GiyFajKGzCBhJQ2ywtxd4ERFJBWvRLw2JpC7n` (Optimism mainnet `@akige`)
- **Bob** (mock): `did:key:z6LSgEv9jNR4iZN1w8P9SmMWgodS2UH3YCNzmTQp6R7FbVX2`
- **Bob vault**: `/var/folders/dq/yvh1xz3s0vv0c8vdklc9_v6h0000gn/T/sisoul-bob.XXXX.gSGnyhLWQK/` (mktemp, 重启会丢)

### 重启 Bob daemon (新会话用)

```bash
BOB_VAULT=$(mktemp -d -t sisoul-bob.XXXX)
cd ~/sisoul-dev

# 1. Bob 用 alice 同 seed 不行, 真 init 新 seed
.venv/bin/sisoul init --vault-dir $BOB_VAULT \
  --goals "lend LLM to alice" --non-interactive
BOB_DID=$(.venv/bin/sisoul did show --vault-dir $BOB_VAULT | grep -oE "did:key:z[A-Za-z0-9]+" | head -1)
echo "Bob did: $BOB_DID"

# 2. Bob 起 daemon 9877 (复用 alice kubo 5001 same-swarm)
( OPENAI_API_BASE="http://127.0.0.1:8765/v1" \
  OPENAI_API_KEY="mock-bob-key" \
  ANTHROPIC_API_BASE="http://127.0.0.1:8765" \
  ANTHROPIC_API_KEY="mock-bob-key" \
  SISOUL_VAULT=$BOB_VAULT \
  nohup .venv/bin/sisoul daemon --port 9877 < /dev/null > /tmp/bob-daemon.log 2>&1 & )
sleep 10

# 3. mock LLM 真起 (一次性, 之后不动)
[ ! -f /tmp/mock-openai.py ] && cp /tmp/mock-openai.py /tmp/  # 见已存在的脚本
( nohup .venv/bin/uvicorn mock-openai:app --host 127.0.0.1 --port 8765 \
  --app-dir /tmp < /dev/null > /tmp/mock-openai.log 2>&1 & )

# 4. Alice 加 Bob 朋友 + 配 perm strong-tie-auto
.venv/bin/sisoul friend add "$BOB_DID" --nickname bob-mock
.venv/bin/sisoul perms set "$BOB_DID" --mode strong-tie-auto \
  --monthly-cap 1000000 --rate-limit 100
```

mock-openai 脚本完整内容见 `/tmp/mock-openai.py` (返 stub chat completion).

---

## 六、PWA 真用户流程 (V2EX 读者真能玩到的)

1. **装 sisoul**: `curl -sSL https://raw.githubusercontent.com/akige/sisoul/main/install.sh | bash`
2. **起 daemon**: `sisoul daemon &`
3. **浏览器开**: `http://127.0.0.1:9876/app/`
4. **`/friends`**: 真 click "+ Add Friend", 真填 DID (或 `@akige` 走 EAS resolve), 真 200, list 真刷新
5. **`/borrow`**: 真选朋友, fill token_count 任意正整数, prompt 任意, 真 click "发起 borrow" → 真返 inflight card stage="完成"
6. **`/lend`**: 真看 pending borrow request, 真 click Approve/Deny
7. **`/ask`**: v2 mock pipeline 真返 answer + Provenance UID

### 真已知 limitation (V2EX 读者会发现的)

| 真状态 | 真原因 | 后续怎么修 |
|---|---|---|
| ⚠️ borrow 真返 "stub-passthrough" 一行字, 不调真 LLM | `dev-B encrypted_proxy 未 ship` (`src/sisoul/friend/borrow.py` 默认 `_default_forwarder` 返 stub) | 接 mock OpenAI: `src/sisoul/friend/encrypted_proxy.py` 真接 openai compat client, 走 Bob daemon 的 `OPENAI_API_BASE` env. P0. |
| ⚠️ borrow 模式 force_mode=strong-tie-auto (Alice 自批准) | Alice CLI/daemon 不真等 Bob GossipSub 响应 | borrow.py 真路径: per-request mode 等 lender approve 经 GossipSub. Bob `_maybe_start_lend_loops` 真 subscribe 但 ingest LendStore 后没真触发 PWA SSE notify. P1. |
| ⚠️ `/sisoul/friend/list` `trust_level` 字段 daemon 端默认 2 | _FriendOut 原 schema 无 trust_level | 加 `daemon_routes/friend.py:_friend_out` 真返 trust_level (from `strong_tie_score` 推导, 或 explicit field). P2. |
| ⚠️ SSE `/notify/stream` 返 heartbeat stub | 真 WS endpoint 存在但 PWA 没接 WS | 写 WS↔SSE bridge 或 PWA 改用 WS. P2. |
| ⚠️ sidebar 折叠 + 没 tooltip, 用户看不到 nav 标签 | localStorage 存 collapsed 状态影响所有 route | `pwa/src/components/Sidebar.tsx` 默认 expanded + 加 tooltip. P3. |
| ⚠️ Borrow/Lend 错误盒 fallback 只 "重试" 链接 | 没 "查看详情 / 报告 issue" 按钮 | 加 GitHub Issues 跳转 + stack trace toggle. P3. |
| ⚠️ `/skills` 默认选 `available (0)` 空 tab | 没 empty state 引导 | 改默认到 `owned (2)` (有内容), 或 empty state 加 "去 IPFS 浏览技能市场 →". P3. |

---

## 七、后续详细计划 (按 P0 → P3 排序, 每条都可独立做)

### P0 (1-2 天) · LLM forward 真打通

**目标**: borrow 不再返 stub, 真转发到 Bob daemon → Bob 调 mock OpenAI → 真返 LLM reply

- [ ] `src/sisoul/friend/encrypted_proxy.py` `_default_forwarder` 真接 openai compat client (用 httpx + Bob env `OPENAI_API_BASE` / `OPENAI_API_KEY`)
- [ ] `src/sisoul/friend/borrow.py` borrow_resource() 检测 EncryptedProxy 真接入, 不再走 stub-passthrough
- [ ] Bob daemon `_maybe_start_lend_loops` 收到 GossipSub lend-request 后真调 `_post_borrow` 内部流程, 自动 approve + forward
- [ ] e2e 真测: Alice PWA Borrow → daemon publish → Bob 收 → mock OpenAI 真返 stub completion → Alice 看到 "Hi from Bob's mock LLM!"

### P1 (3-5 天) · per-request mode 真打通

**目标**: Alice 发 borrow → Bob daemon 真 ingest LendStore → PWA SSE 真推 lend.request 事件 → Alice 看 "Bob 正在 review" → Bob approve 后真 forward → Alice 真返回

- [ ] `src/sisoul/daemon.py` `_maybe_start_lend_loops` 真 ingest 后调 EventBus 触发 SSE notify
- [ ] PWA `/sisoul/notify/stream` 真返 `lend.request` / `borrow.update` events (现在是 heartbeat-only)
- [ ] PWA Lend.tsx 真 SSE 监听, "新借用请求" toast 弹出
- [ ] borrow.py 真支持 timeout 5min 等 Bob, 不强制 force_mode

### P2 (1-2 周) · V2EX 发文 + 真用户上手

- [ ] `docs/V2EX-LAUNCH-POST.md` 已经准备好, 真 ship 路径就缺 P0 LLM forward
- [ ] V2EX 帖子里 etherscan + easscan 链接真验证可点
- [ ] 加 docs/FAQ.md (用户问"PWA 白屏 / borrow 没反应" 真预案)
- [ ] 真发到 V2EX `分享` 节点, 真追踪 issue
- [ ] 预备 hotfix 流程: V2EX 用户报 bug → 真用 playwright 抓 → daemon side alias 快速修 → push

### P3 (2-3 周) · 体验 polish

- [ ] Sidebar 默认展开 + tooltip + 用户 toggle 持久化
- [ ] Error fallback "查看详情" + GitHub Issues 跳转
- [ ] iOS/Android native app (mobile/ 已有 skeleton, 缺 SwiftUI/Compose UI)
- [ ] win11 .exe + winget package
- [ ] mac .dmg + Homebrew Cask
- [ ] 接真 EAS Optimism username discovery (PWA `@username` 搜索)
- [ ] 接真 USDT micropay (`lend_auto_approve.py` 已 ship, 但 PWA UI 没接)

### P4 (long-term · roadmap 里)

- [ ] TEE forward (NVIDIA H100 CC enclave, Alice 自己也看不到 prompt)
- [ ] DAO governance Sepolia deploy
- [ ] 真 P2P NAT traversal 用户实测 (现在 same-host 测过, 跨 NAT 没测)
- [ ] PWA 移动端响应式 (现在桌面 only)

---

## 八、关键文件 reference

### 后端 (daemon)

| 文件 | 真用途 |
|---|---|
| `src/sisoul/daemon.py` | FastAPI app + 7 个 PWA compat alias (line 90-300) + SPA fallback (308 redirect /app) + CORS |
| `src/sisoul/daemon_routes/friend.py` | `/friend/list` 路由 + 我加的 didkey_friends.json 合并逻辑 (line 262-310) + `_post_borrow` (line 573) |
| `src/sisoul/friend/borrow.py` | borrow_resource() 主流程 · `_safe_notify` GossipSub publish (我之前加) · L55-110 |
| `src/sisoul/friend/encrypted_proxy.py` | **P0 待修**: `_default_forwarder` 真接 mock OpenAI |
| `src/sisoul/friend/lend_gossipsub.py` | publish/subscribe lend-request/ack (A3 真路径) |
| `src/sisoul/friend/lend_auto_approve.py` | v1.1 TronGrid 30s 轮询自动批 (opt-in: `sisoul lend auto-approve enable`) |
| `src/sisoul/onchain/username_eas.py` | mainnet EAS register · 含 schema-on-demand register |
| `src/sisoul/chat/transport.py` | `set/get_default_transport` singleton (daemon 进程内 share kubo pubsub) |

### 前端 (PWA)

| 文件 | 真改 |
|---|---|
| `pwa/src/App.tsx` | Router runtime detect base (line 32-44) |
| `pwa/src/api/daemon.ts` | 9 个 adapter (preferences/goals/chat-history/identity/attest/friends/perms/borrow/lend) |
| `pwa/src/routes/Borrow.tsx` | submit handler 真兜底 `session?` shape (line 156-193) |
| `pwa/src/routes/Lend.tsx` | int epoch sort + approve onSubmitted |
| `pwa/src/components/OnboardingScreen.tsx` | daemon offline 时显示 5 步装机 + 复制按钮 |
| `pwa/vite.config.ts` | `base: "./"` 相对路径 |
| `pwa/index.html` | no-cache meta + SW unregister 代码 |

### 工具 & CI

| 文件 | 真用途 |
|---|---|
| `install.sh` | 274 行, 真测过 mac+linux+wsl2 |
| `Formula/sisoul.rb` | Homebrew formula, `brew audit --strict` 0 offense |
| `tools/menubar/sisoul_tray.py` | macOS menu-bar tray (rumps) |
| `tools/menubar/build_app.sh` | py2app 真打 Sisoul.app (29MB) |
| `.github/workflows/pages.yml` | GH Action build PWA + deploy GH Pages |
| `pwa/_full_audit.mjs` | 综合 14 路由 audit script (新会话可复用) |

### 文档

| 文件 | 真用途 |
|---|---|
| `docs/V2EX-LAUNCH-POST.md` | V2EX 发文 final · mainnet/menubar/install/PWA 全 ✅ |
| `docs/INSTALL.md` | 装机 SOP · 一行 install + 4 步源码 + borrow/lend 用法 |
| `docs/HANDOFF-V1.0-STABLE-DEV.md` | 前一份 handoff (2026-06-06, aws-us 给的) |
| **`docs/HANDOFF-2026-06-10-PWA-E2E-COMPLETE.md`** | **本份** (新会话从这读) |

---

## 九、真证据链接 (V2EX 帖子可贴)

- **PWA dashboard**: https://akige.github.io/sisoul/ (GH Pages, daemon offline 时显 Onboarding) + http://127.0.0.1:9876/app/ (daemon online same-origin)
- **Mainnet @akige tx**: https://optimistic.etherscan.io/tx/0xabcb1bab93946d491503a6e1368ee8c6b870085e185eed83f629459d865bb72c
- **EAS attestation**: https://optimism.easscan.org/attestation/view/0x78375e7ed6cbec575f630be8e32377da91de4801e6f9799bfb16a7c71ca6acdb
- **GitHub repo**: https://github.com/akige/sisoul (Apache-2.0)
- **install.sh**: `curl -sSL https://raw.githubusercontent.com/akige/sisoul/main/install.sh | bash`

---

## 十、Git 状态 + 双 remote

```
HEAD: 07967acee0c19fd4e76531dafd8dfd9e867ffd40
github main:      07967acee0c19fd4e76531dafd8dfd9e867ffd40  ✓ sync
aws-us-bare main: 07967acee0c19fd4e76531dafd8dfd9e867ffd40  ✓ sync
```

今天累计 34 commit (cfb2d647..07967ace), 全部双 push.

---

## 十一、新会话第一句怎么开 (template)

```
我接手 sisoul 项目, 已读 docs/HANDOFF-2026-06-10-PWA-E2E-COMPLETE.md.

当前真状态:
- 14/14 PWA route 0 error
- mainnet @akige 真上链
- 4-daemon e2e 环境真打通到 stub-passthrough

我的任务: <用户告诉你的目标>

第一步真做: <按 P0/P1/P2 顺序选>
```

---

## 十二、提醒新会话 (硬规)

1. **真测真证据** — 每个改动必须 playwright headless 真验证 0 error
2. **不要"应该修好了"** — 用户极不耐烦, 反复骂这词, 必须真贴 audit 输出
3. **双 remote push** — github + aws-us-bare 任何 commit 都同步
4. **daemon 重启用 `( nohup ... < /dev/null & )` 子 shell** — 不用 `setsid` (mac 没有), 不用 `pkill -f` regex (\| 在 basic regex 不 alternation)
5. **PWA 改动后真 rebuild** (`cd pwa && npm run build`) + daemon mount 真 reload 才生效
6. **mock LLM 在 /tmp/mock-openai.py** — 真路径 alpha 简化, 不真接 Anthropic/OpenAI 防止用户 LLM key 泄露
7. **MEMORY.md 三层保护** — 不许精简 (chflags uchg + hook + git)
8. **V2EX 发文前**: P0 LLM forward 真接通才发, 否则用户截图截到 "stub-passthrough" 一行字会反弹

---

**handoff doc 终.**

---

## 十三、Addendum 2026-06-10 (接手会话 · P0 完成)

**P0 LLM forward 已真打通** (commit `11b25f93` + `4a84318c`, 双 remote sync):

| 原 limitation | 现状 |
|---|---|
| borrow 返 stub-passthrough | ✅ **已修**: 新 `friend/proxy_p2p.py` GossipSub 加密往返 (Box X25519 did:key), Bob daemon 真调自己的 LLM endpoint (`OPENAI_API_BASE`). e2e 真证据: UI 卡片显示 "Hi from Bob's mock LLM!". lender 离线时优雅降级 stub (文本明示原因) |
| force_mode=strong-tie-auto | 🟡 部分: proxy 真等 Bob 响应 + Bob 真调 LLM, 但审批仍 strong-tie-auto 自动 (per-request 在 lender 端返明确 denied). P1 不变 |
| trust_level 默认 2 | ⏳ P2 不变 |
| SSE heartbeat stub | ⏳ P2 不变 |
| sidebar 折叠无 tooltip | ✅ 已加 title tooltip (默认本来就是展开, 原表述过时) |
| 错误盒只有"重试" | ✅ AsyncBoundary 默认 fallback 加 "报告 issue →" (GitHub Issues 预填 title/body) |
| /skills 默认空 tab | ✅ 核实默认已是 `owned` (原表述过时, 无需改) |

**新发现 + 修掉的 bug** (本次接手会话):
1. `borrow._proxy_call` import 的 `proxy_chat_request` 模块函数从未存在 → borrow 永远 stub (P0 根因)
2. `/borrow/run` alias 在 event loop 里同步跑 `_post_borrow` → 改 `to_thread`
3. `OpenAIAdapter` 不读 `OPENAI_API_BASE` env → Bob 的 mock 指向不生效
4. `_normalize_did` 把 `did:key:…` 包成 `did:sisoul:did:key:…` 写库 → PWA UI borrow 必 proxy-failed (写侧修 + 读侧 3 处解残留)
5. Borrow.tsx 丢弃 `proxy_text` → 用户永远看不到借来的 LLM 回复
6. 5 个环境依赖测试假失败 (有真 vault/daemon 的机器必炸) → 密闭化

**验收**: pytest 2372 pass 0 fail · vitest 177 pass · PWA audit 14/14 0 error · playwright UI e2e 真见 mock LLM 回复.

**V2EX gate (§十二-8)**: P0 已达成, 发文不再有"截图 stub"反弹风险. 下一批次 = P1 per-request (3-5 天) 或直接走 P2 发文流程.

---

## 十四、Addendum 2026-06-10 下午 (B2 P1 + B3 发文准备 全完成)

**B2 · per-request 真审批全链路** (commit `1a2700c5`):
- `daemon_events.py` EventBus → `/sisoul/notify/stream` 真 SSE (lend.request / lend.update / borrow.update)
- lender ingest → SSE 实时推 Lend 页卡片; approve/deny 路由回发 GossipSub ack
- 借入方 ack loop 落地本地 LendStore 解锁轮询; lender 端 serve 复核审批状态
- PWA Borrow 加审批模式选择器 ("自动批准" / "等对方批准 120s"), 等待态按钮文案
- 双页 playwright e2e: Bob 实时收卡 → Approve → Alice 真回复; Deny → Alice 0s 收 "lender-denied|不借"

**B2 顺带修的 4 个深层 bug**:
1. LendStore 默认路径不认 SISOUL_VAULT (双 daemon 串库)
2. `_safe_notify` 在 threadpool 跨 event loop 复用 httpx AsyncClient → GossipSub 消息**静默丢** (flaky 根因)
3. SSE 流 `wait_for(gen.__anext__())` 30s 后打死 generator → ERR_INCOMPLETE_CHUNKED_ENCODING
4. PWA 8 个文件硬编码 127.0.0.1:9876 (daemon 托管时改同源, 非默认端口/Bob daemon 全修好)

**安全收口** (commit `b389d739`): lender serve gate 加好友校验 — 陌生人 (任意 did) 不能再烧 lender 配额, 反向真测 `denied: borrower is not in lender's friend list`.

**B3 · 发文准备** (commit `26586193`):
- `docs/TROUBLESHOOTING.md` 中文排错手册 (每条带可复制诊断命令)
- `docs/HOTFIX-PLAYBOOK.md` 发文后 bug 响应 SOP (分级/复现/验证 gate/回帖模板)
- V2EX-LAUNCH-POST 补 2 条 2026-06-10 新能力 + 修占位 brew URL + 链接排错手册
- 帖内 14 外链全验 HTTP 200 (tronscan 403 = bot 防护, 浏览器正常); GH Pages 连续 deploy 绿

**最终验收**: pytest 2372 pass 0 fail · vitest 177 pass · PWA audit 14/14 0 error · 三路 e2e (strong-tie / per-request approve / per-request deny / 陌生人拒) 全真证据.

**发文剩余唯一动作**: 用户把 `docs/V2EX-LAUNCH-POST.md` 发到 V2EX 分享节点.
