# Hotfix Playbook · V2EX 发文后 bug 快速响应 (2026-06-10)

> 目标: 用户报 bug → 复现 → 修 → 验证 → push, 全程 < 2 小时.

## 1. 接报分级

| 级 | 标准 | 响应 |
|---|---|---|
| P0 | install.sh 跑不通 / PWA 白屏 / daemon 起不来 | 立即修, 修完 @ 楼主 |
| P1 | borrow/lend/friends 某条路径报错 | 当天修 |
| P2 | UI 瑕疵 / 文案 / 体验 | 攒批次修 |

## 2. 复现 (本地 4-daemon 环境)

```bash
# Alice daemon (默认 vault)
cd ~/sisoul-dev && ( nohup .venv/bin/sisoul daemon < /dev/null > ~/.sisoul/daemon.log 2>&1 & )

# Bob daemon (独立 vault + mock LLM) — 见 docs/HANDOFF-2026-06-10-PWA-E2E-COMPLETE.md §五
# kubo:  ipfs daemon --enable-pubsub-experiment &
# mock:  uvicorn mock-openai:app --port 8765 --app-dir /tmp
```

PWA 问题用 playwright 抓:

```bash
cd pwa && node _full_audit.mjs          # 14 路由全扫 (0 bugs 才算干净)
node _per_request_e2e.mjs               # 双页 per-request 审批 e2e
node _borrow_ui_e2e.mjs                 # 单页 strong-tie borrow e2e
```

## 3. 修复纪律

1. 找根因, 不打表面补丁 (历史教训: did 双前缀 / httpx 跨 loop 静默丢消息都是根因层)
2. PWA 改动后必须 `cd pwa && npm run build` (daemon serve 的是 dist/)
3. daemon 代码改动后重启两个 daemon 才生效 (editable install)

## 4. 验证 gate (全过才能 push)

```bash
.venv/bin/python -m pytest tests/ -q      # 期望: 0 failed
cd pwa && npx vitest run                  # 期望: 0 failed
node _full_audit.mjs                      # 期望: grand total bugs: 0
```

## 5. 发布

**用户可见的修复必须 bump 版本号** (两处同步: `pyproject.toml` + `src/sisoul/__init__.py`
的 `__version__`) — 用户的更新通知 (`sisoul update` / PWA 角标) 靠比对 main 分支
pyproject version, 不 bump 用户永远不知道有新版.

```bash
git add -A && git commit -m "fix(...): <根因一句话>"
git push github main                                    # (维护者另推备份 remote)
gh run watch --repo akige/sisoul                        # Pages auto-deploy 绿
```

GH Pages PWA 自动跟 main (`.github/workflows/pages.yml`), push 即发布, ~30s.

## 6. 回帖模板

> 已修: <一句话根因>. commit <hash>.
> 升级: `curl -sSL https://raw.githubusercontent.com/akige/sisoul/main/install.sh | bash` 重跑即可.
> PWA 用户强刷一次 (Cmd+Shift+R).

## 7. 回滚

```bash
git revert <bad-commit> && git push github main
cd pwa && npm run build
```
