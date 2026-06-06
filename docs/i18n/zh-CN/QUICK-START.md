# sisoul 快速开始

> 5 分钟首次运行指南。从 `install.sh` 装完开始。

## 1. 装机

```bash
curl -sSfL https://github.com/akige/sisoul/releases/latest/download/install.sh | bash
```

验证:

```bash
sisoul --version
```

应该看到 ASCII box 显示 `sisoul 1.0.0-alpha`。

## 2. 首次设置 (`sisoul init`)

```bash
sisoul init
```

5 步引导:

1. **Petname** (本地昵称) — 默认 = hostname
2. **did:key** — 自动生成 ed25519 身份 (不注册 / 不要邮件 / 不要密码)
3. **LLM provider** — 9 选 1: anthropic / openai / gemini / copilot-oauth / ollama / akash / bittensor / hyperbolic / litellm-generic / skip
4. **Daemon 模式** — background (推荐) 或 foreground
5. **QR** — 生成 PNG 给朋友扫

结果: `~/.sisoul/` 创建, 含 `did_key.json`, `petnames.json`, `dna.json`。

## 3. 启动 daemon

```bash
sisoul daemon --host 127.0.0.1 --port 9876
```

会看到 sisoul ASCII banner + endpoint URLs。

或装成系统自启 (Linux systemd / Mac launchd):

```bash
bash ops/init/install-autostart.sh
```

## 4. 验证一切 OK

```bash
sisoul health      # daemon + 17 v2 endpoints + Prometheus metrics
sisoul demo        # 8 步端到端演示 (case → search → attest → debate → lesson)
sisoul stats       # 本机计数 (case/skill/friend/petname/lesson)
sisoul cheatsheet  # 19 命令一页参考
```

## 5. 加第一个朋友

### 方式 A: 局域网 (mDNS)

```bash
sisoul friend-discover  # 扫描 5 秒, 列局域网上的 sisoul peers
sisoul friend petname set <did:key:z6Mk...> Alice
```

### 方式 B: QR 码

你生成, 朋友扫:

```bash
sisoul friend qr --out ~/sisoul-add-me.png  # 发 PNG 给朋友
```

朋友扫 (在他们 PWA 或 `sisoul friend qr-scan`):

```bash
sisoul friend qr-scan ~/path/to/your-qr.png
```

### 方式 C: DID 手输

```bash
sisoul invite --did did:key:<你的> --petname <你的>
# 通过 IM / Slack / Discord 分享生成的文本
# 朋友跑:
sisoul friend add did:key:<你的>
```

## 6. 用起来

```bash
# 问 LLM (用你 provider, 或没 key 时借朋友的)
sisoul ask "Rust async tokio::select 死锁怎么修?"

# Multi-agent debate (v3.0 preview, 当前 3 轮 mock)
sisoul debate "PostgreSQL pgbouncer + sqlx prepared statement 怎么修?"

# Chat (Signal 级 E2E, 后量子混合)
sisoul chat send did:key:z6MkFriend "想试试 sisoul 吗?"
sisoul chat recv

# 借朋友 LLM (你没 API key 时)
sisoul borrow request did:key:z6MkFriend
```

## 7. 浏览数据

```bash
sisoul case list                  # 所有 case (CLI)
sisoul case search "tokio"        # 搜索
sisoul case show <case-id>        # 完整详情
```

或浏览器打开 PWA:

```
http://127.0.0.1:9876/docs                   # FastAPI Swagger UI
https://akige.github.io/sisoul/         # PWA dashboard
```

PWA 路由:
- `/dashboard/v2` — growth 曲线 + case/skill 统计
- `/ask` — 走 case 检索 + EAS attest
- `/debate` — multi-agent debate UI
- `/skills/v2` — skill marketplace 浏览 + 装
- `/stats` — Prometheus metrics dashboard
- `/cheatsheet` — CLI 参考 (浏览器版)

## 8. 备份

```bash
sisoul backup --out ~/sisoul-2026-06-04.zip
```

## 9. 求助

```bash
sisoul <command> --help     # 任何命令
sisoul cheatsheet           # 19 命令
sisoul --version-json       # JSON 版本信息
```

## 10. Shell 自动补全 (可选)

```bash
# bash
sisoul completion bash --install
# 在 ~/.bashrc 加: source ~/.bash_completion.d/sisoul

# zsh
sisoul completion zsh --install
# 在 ~/.zshrc 加: source ~/.zsh/completions/_sisoul

# fish
sisoul completion fish --install
```

## Alpha 不包含的 (设定期望)

- ❌ Multi-agent debate 真 LLM (当前: 3 轮 mock synthesize)
- ❌ Personal LoRA 训练 (v2.0 末 T+12m)
- ❌ ChromaDB 向量 embed (当前: TF-IDF foundation)
- ❌ Optimism mainnet DAO / SIS 真发币 (v1.0 stable T+6m)
- ❌ Native Android/iOS app (beta v1.1 T+1m)
- ❌ Hot-load skill marketplace (v2.0 T+11m)

详细路线图: `README.md` § Roadmap 和 `obs §61 / §67`.

## 故障排查

| 症状 | 解决 |
|---|---|
| `sisoul: command not found` | 把 `~/.local/bin` 加进 PATH |
| daemon port 9876 已占用 | `lsof -i :9876` 看占用进程, kill 它, 或 `sisoul daemon --port 9999` |
| `health` 说 daemon unreachable | daemon 没启 — 跑 `sisoul daemon` foreground 看 error |
| `friend-discover` 找到 0 peers | 同 LAN + 同 subnet 需 mDNS; 防火墙拦 UDP 5353? |
| PQXDH "shim mode" 警告 | `pip install kyber-py` 用真 ML-KEM-1024 |
| 跨 NAT borrow 失败 | `sisoul net status` 看 kubo IPFS peer 数 |

## 下一步: 加入 alpha 网络

daemon 起来后:

1. 把你的 DID 给朋友 (sisoul invite 生 text/QR)
2. 加 2-3 个朋友建朋友圈
3. 朋友有 API key 时试 `sisoul borrow request <朋友-did>`
4. 写你第一个 case: 用 `sisoul ask` 问个问题, daemon 自动记录
5. 看 growth: PWA `/dashboard/v2` 或 `sisoul stats`

欢迎加入网络。⚡
