# sisoul PWA dashboard · 内部 README

> Phase 2 W23-W28 · dev-C ship. 内部 staging, 不发外网.

## 是什么

sisoul 元层 dashboard. SolidJS + Vite + Tailwind, 5 路由:

- `/` Vault — 浏览 `~/.sisoul/preferences/*.md`
- `/goals` Goals — 长期目标 + 进度
- `/chat` Chat history — chat 时间线 + session 详情
- `/settings` Settings — vault / daemon / DID / LLM provider
- `/advanced` Advanced — Phase 3 链上 attestation + Phase 4 朋友 (placeholder)

PWA 可装机 (Add to Home Screen on iOS / install on desktop Chrome), 支持 offline shell 缓存.

## 前置

- Node >= 20 (这次开发用 v22.22.2)
- npm >= 10 (10.9.7)
- sisoul daemon 跑着 (默认 `127.0.0.1:9876`, 由 `python -m sisoul.daemon` 或 `sisoul daemon start` 启动)

## 装依赖

```bash
cd dev/sisoul/pwa
npm install
```

(约 280 包, 12s)

## 开发跑

```bash
npm run dev
```

Vite 起在 `http://127.0.0.1:5173`. 已配 proxy 把 `/sisoul/*` 转 daemon `:9876`.

## 生产构建

```bash
npm run build
# 输出 dist/  (静态文件, 可直接 nginx / daemon mount 上)
npm run preview
# 起 4173 预览
```

## 测试

### 单元 (vitest)

```bash
npm run test          # 33 tests pass
npm run test:watch    # watch mode
```

覆盖: utils/format · api/daemon · GoalProgressBar · ChartSimple.

### e2e (playwright)

```bash
npx playwright install --with-deps chromium webkit   # 首次装浏览器 (sandbox 可能要主集成补)
npm run test:e2e
```

测 5 路由可点 + 移动菜单 + manifest + sw 可达. 配 3 profile (chromium-desktop / iPad / iPhone 15).

## TypeScript 检查

```bash
npm run typecheck   # tsc --noEmit, 应 0 错
```

## PWA 装机

1. 生产 build + 起 https/localhost serve (或 daemon serve dist/)
2. Chrome desktop 地址栏右侧"安装"按钮
3. iOS Safari `分享 → 添加到主屏幕` (manifest 已配 apple-touch-icon + standalone)

Service worker 缓存策略: 静态 cache-first, daemon API network-first (offline 返 503 JSON), HTML navigation network-first → shell fallback.

## 集成到 daemon

PWA 是独立 SPA. 主 daemon (src/sisoul/daemon.py) 需 `include_router` 装 `daemon_routes.pwa.router` 才能让 PWA 拿数据. 详 `docs/daemon-api-spec.md`.

集成示例 (主集成做):

```python
from fastapi import FastAPI
from sisoul.daemon_routes.pwa import router as pwa_router

app = FastAPI()
app.include_router(pwa_router)
```

可选: build 后的 `dist/` 直接挂到 daemon 作为静态文件:

```python
from fastapi.staticfiles import StaticFiles
app.mount("/", StaticFiles(directory="pwa/dist", html=True), name="pwa")
```

## 项目布局

```
pwa/
├── index.html              # SPA shell + PWA meta
├── package.json
├── vite.config.ts          # Vite + proxy + vitest config
├── tailwind.config.js      # sisoul 主题 + ipad/ipadpro breakpoints
├── tsconfig.json
├── playwright.config.ts
├── public/
│   ├── manifest.json       # PWA manifest
│   ├── icon-192.svg
│   ├── icon-512.svg
│   └── sw.js               # service worker (shell + API offline)
├── src/
│   ├── main.tsx            # entry
│   ├── App.tsx             # router + layout
│   ├── index.css           # tailwind + utilities
│   ├── api/
│   │   └── daemon.ts       # daemon fetch wrapper + 类型
│   ├── components/
│   │   ├── Sidebar.tsx
│   │   ├── TopBar.tsx
│   │   ├── GoalProgressBar.tsx
│   │   ├── ChartSimple.tsx
│   │   └── AsyncBoundary.tsx
│   ├── routes/
│   │   ├── Vault.tsx
│   │   ├── Goals.tsx
│   │   ├── ChatHistory.tsx
│   │   ├── Settings.tsx
│   │   └── Advanced.tsx
│   └── utils/
│       └── format.ts
├── tests/
│   ├── unit/               # vitest (jsdom + @solidjs/testing-library)
│   └── e2e/                # playwright (chromium + iPad + iPhone)
└── docs/
    └── daemon-api-spec.md  # 列 PWA 用的 daemon endpoints
```

## 已知 TODO (后续 phase / 主集成补)

- 主集成 do `daemon.py: include_router(pwa.router)`
- playwright 浏览器二进制装 (`npx playwright install chromium`) — sandbox 装不上 → qa-D / 主集成 补
- IPFS + ENS deploy (PWA on `<handle>.sisoul.eth.limo`) — 留 v1.1
- service worker 高级 features (background sync / push / periodic sync) — Phase 3
- chat 渲染目前 `<pre>` 显示, 后续可加 markdown 渲染 (留 Phase 3)
- 真"chart.js"级别图表 — 目前 ChartSimple SVG 占位足够, 高级图表 (折线/烛图) 留 Phase 3
