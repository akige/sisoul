# sisoul 排错手册 · alpha (2026-06-10)

> V2EX / GitHub 用户最常撞到的问题, 每条都给可复制的诊断命令.
> 概念类问题 (为什么不发币 / 跟 ChatGPT 区别) 见 [FAQ.md](FAQ.md).

---

## 装机 & 启动

### `curl … install.sh | bash` 跑完, `sisoul` 命令找不到?

新开一个终端 (install.sh 把 `~/.local/bin` 写进了 shell rc, 当前终端不生效), 或:

```bash
export PATH=$HOME/.local/bin:$PATH
sisoul --version
```

### `sisoul daemon` 起不来 / 端口被占?

```bash
lsof -i :9876               # 看谁占着
sisoul daemon --port 9999   # 换端口跑 (PWA 开 http://127.0.0.1:9999/app/)
```

### macOS 默认 Python 3.9 装不上?

要 Python 3.11+. `brew install python@3.12` 后重跑 install.sh, 它会自动探测.

---

## PWA dashboard

### 浏览器打开 PWA 白屏?

1. 优先走 daemon 同源地址: `http://127.0.0.1:9876/app/` (不是 GitHub Pages)
2. 强刷一次: `Cmd+Shift+R` / `Ctrl+Shift+R` (老 Service Worker 缓存会被自动清, 但强刷最快)
3. 还白 → F12 Console 截图发 [Issues](https://github.com/akige/sisoul/issues)

### GitHub Pages 版 (akige.github.io/sisoul) 显示 "daemon offline"?

正常 — Pages 是 https, 浏览器拦 http://127.0.0.1 混合内容. 跟着 onboarding
卡片装好 daemon 后, 直接用 `http://127.0.0.1:9876/app/` (同源, 全功能).

---

## 借 LLM (borrow / lend)

### borrow 发起后返回 "[stub-passthrough] 真转发未达 lender …"?

这是优雅降级 — 对方 daemon 没收到你的加密请求. 按顺序排查:

```bash
# 1. 你本机 kubo 活着吗 (GossipSub 底座)?
curl -s -X POST http://127.0.0.1:5001/api/v0/id | head -c 100   # 有 JSON = 活

# 2. 对方 daemon 活着吗? 让对方跑:
sisoul status

# 3. 跨 NAT 时两边 kubo 要能 swarm 互联:
ipfs swarm peers | head
```

### per-request 模式等了 2 分钟超时?

对方没在 2 分钟内点 Approve. 对方的 Lend 页 (`/app/lend`) 会实时弹卡片 —
确认对方真开着页面; 或让对方用 CLI: `sisoul lend list` + `sisoul lend approve <id>`.

### 我是借出方, 朋友借到的回复是 failed / 空的?

借出方 daemon 用**自己的** LLM key 真调模型. 启动 daemon 前配好:

```bash
export OPENAI_API_KEY=sk-...   # 或 OPENAI_API_BASE 指你的 openai 兼容 endpoint
sisoul daemon &
```

没配 key → 借入方收到 failed 响应 (你的 key 永远不发给对方, 只有回复文本过去).

### 借出方能看到我的 prompt 吗?

不能持久看到: prompt 用 libsodium Box (X25519, 密钥 = did:key 本体) 端到端加密,
借出方 daemon 只在**内存**解密后转发给 LLM, 不落盘不进 log
(`src/sisoul/friend/encrypted_proxy.py` 有 `enforce_no_disk_write` 自检, 代码可审).
注意: 借出方机器本身是可信边界 — 它毕竟要把明文发给 LLM API.

---

## 朋友 & 身份

### 加好友后列表里没刷出来?

Friends 页右上刷新, 或 `sisoul friend list`. 还没有 → 看 daemon log:
`tail -20 ~/.sisoul/daemon.log`.

### `@username` 解析失败?

`sisoul username resolve <name>` 走 Optimism mainnet EAS GraphQL — 需要网络可达
`optimism.easscan.org`. 没注册过的名字解析不到是预期.

### 12 词助记词丢了?

没有任何人能找回 (这是 0 服务器架构的代价). `sisoul backup` 的 zip 也能恢复.
新机器: `sisoul restore <12 words>`.

---

## 其他

### 卸载?

```bash
pkill -f "sisoul daemon"; rm -rf ~/sisoul-app ~/.sisoul ~/.local/bin/sisoul
```

没有服务器, 没有账号注销流程 — 删完就是没了.

### 报 bug 怎么给信息?

```bash
sisoul --version-json
tail -50 ~/.sisoul/daemon.log
```

贴这两段 + 复现步骤到 [Issues](https://github.com/akige/sisoul/issues). UI 问题加截图.
