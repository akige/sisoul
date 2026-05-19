"""sisoul · helia IPFS 适配 (PWA 浏览器端 + Node.js subprocess).

§32 §B.3 · sisoul v1.0-decentralized 方案 PWA 路径.

# 设计

helia (https://github.com/ipfs/helia) 是 IPFS Foundation 维护的 JS 实现, js-ipfs 后继.

本模块作用:
1. **PWA**: 提供 helia 配置 (bootstrap / blockstore / 网络选项) 给 PWA 前端代码引用.
   PWA 真正的 helia binding 在 `pwa/src/ipfs/helia.ts` (TypeScript, 浏览器内).
   本 Python 模块给 Python 后端 / daemon route 生成 PWA 用的 helia config JSON.
2. **Node.js subprocess (备路径)**: 当用户机器装了 Node.js (>=18) 但没装 kubo, 通过
   `node helia-daemon.js` subprocess 跑 helia (Node 端), 暴露跟 kubo `:5001` 兼容的
   HTTP API 子集 (add/cat/pin/id). sisoul daemon 透明替换 IPFSKuboNode.

# 决策 (详 §B.3.2)

主路径用 kubo, helia 备用. 因为:
- helia 维护风险 (§34 C8): JS-IPFS 团队解散过, helia 长期 vs kubo Go (Protocol Labs).
- 内嵌 Node runtime 让 Python 包多 100MB+, 不值.

PWA 是唯一刚需 helia 的路径: iOS Safari 不允许跑 daemon, 必须浏览器内嵌.

# 模块边界

- 不真 spawn Node subprocess (Wave A 不引入 Node 依赖, 仅生成 config + 文档).
- 真嵌入在 `pwa/src/ipfs/helia.ts` (TypeScript, 200 行, agent-3 不写 TS, 留 stub).
- 本 Python 模块: HeliaConfig 数据类 + 序列化 + 默认 bootstrap.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 常量: PWA helia 默认 bootstrap (含 webtransport / webrtc-direct)
# ─────────────────────────────────────────────────────────────────────────────


# PWA / 浏览器只能走 WSS / webtransport / webrtc, 不能 raw TCP.
# 这些 bootstrap 都暴露了 WSS multiaddr.
DEFAULT_HELIA_BOOTSTRAP: tuple[str, ...] = (
    # IPFS Foundation 官方 webtransport bootstrap (helia 默认)
    "/dnsaddr/bootstrap.libp2p.io/p2p/QmNnooDu7bfjPFoTZYxMNLWUQJyrVwtbZg5gBMjTezGAJN",
    "/dnsaddr/bootstrap.libp2p.io/p2p/QmQCU2EcMqAqQPR2i9bChDtGNJchTbq5TbXJJ16u19uLTa",
    "/dnsaddr/bootstrap.libp2p.io/p2p/QmbLHAnMoJPWSCR5Zhtx6BHJX9KiKNN6tpvbUcqanj75Nb",
    "/dnsaddr/bootstrap.libp2p.io/p2p/QmcZf59bWwK5XFi76CZX8cbJ4BhTzzA3gU1ZjYZcYW3dwt",
    # webtransport peer (helia default)
    "/dns4/wrtc-star.discovery.libp2p.io/tcp/443/wss/p2p-webrtc-star",
)

# 公共 gateway (浏览器 fetch fallback)
DEFAULT_PUBLIC_GATEWAYS: tuple[str, ...] = (
    "https://ipfs.io/ipfs/{cid}",
    "https://cloudflare-ipfs.com/ipfs/{cid}",
    "https://dweb.link/ipfs/{cid}",
    "https://gateway.pinata.cloud/ipfs/{cid}",
    "https://w3s.link/ipfs/{cid}",
)


HeliaTransport = Literal["webtransport", "webrtc", "websocket", "circuit-relay"]


@dataclass
class HeliaConfig:
    """PWA helia 初始化配置.

    PWA `pwa/src/ipfs/helia.ts` 读这个 JSON 生成 createHelia({...}) 调用.

    字段:
    - bootstrap: multiaddr 列, 启动连这些 peer
    - public_gateways: HTTP fallback gateway (无 peer 时走)
    - transports: 启的 transport 类型 (浏览器: webtransport+webrtc+ws)
    - dht_enabled: DHT 客户端 (PWA 一般 client 模式, 不 serve)
    - blockstore: indexeddb (浏览器内) / fs (Node)
    - datastore: indexeddb / fs
    """

    bootstrap: list[str] = field(default_factory=lambda: list(DEFAULT_HELIA_BOOTSTRAP))
    public_gateways: list[str] = field(default_factory=lambda: list(DEFAULT_PUBLIC_GATEWAYS))
    transports: list[HeliaTransport] = field(default_factory=lambda: [
        "webtransport", "webrtc", "websocket", "circuit-relay"
    ])
    dht_enabled: bool = True
    dht_client_mode: bool = True  # PWA: 只查不 serve
    blockstore: Literal["indexeddb", "fs", "memory"] = "indexeddb"
    datastore: Literal["indexeddb", "fs", "memory"] = "indexeddb"
    pubsub_enabled: bool = False  # PWA 默认关 (流量大)
    autonat_enabled: bool = True
    swarm_listen: list[str] = field(default_factory=lambda: [
        "/webrtc",
        "/p2p-circuit",
    ])
    # 浏览器侧不可设 (Node helia 子进程时才用)
    api_port: Optional[int] = None
    gateway_port: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def for_pwa(self) -> dict[str, Any]:
        """生成 PWA 端可直读的 helia config (剔 Node-only 字段)."""
        d = self.to_dict()
        d.pop("api_port", None)
        d.pop("gateway_port", None)
        return d

    def for_node(self, *, api_port: int = 5101, gateway_port: int = 8180) -> dict[str, Any]:
        """生成 Node helia daemon 用的 config (含 HTTP API 端口)."""
        d = self.to_dict()
        d["api_port"] = api_port
        d["gateway_port"] = gateway_port
        # Node 端用 fs blockstore (而非 indexeddb)
        d["blockstore"] = "fs"
        d["datastore"] = "fs"
        d["swarm_listen"] = [
            "/ip4/0.0.0.0/tcp/4002/ws",
            "/ip4/0.0.0.0/udp/4002/webrtc-direct",
            "/p2p-circuit",
        ]
        return d


# ─────────────────────────────────────────────────────────────────────────────
# PWA config 文件生成
# ─────────────────────────────────────────────────────────────────────────────


def write_pwa_helia_config(
    target_dir: Path,
    *,
    bootstrap: Optional[tuple[str, ...]] = None,
    public_gateways: Optional[tuple[str, ...]] = None,
    filename: str = "helia-config.json",
) -> Path:
    """把 helia config JSON 写到 PWA public 目录, PWA 启动时 fetch.

    Args:
        target_dir: PWA public dir (e.g. pwa/public/ipfs/).
        bootstrap: 自定 bootstrap.
        public_gateways: 自定 gateway.
        filename: 文件名.

    Returns:
        写好的 JSON 文件路径.
    """
    target_dir = Path(target_dir).expanduser()
    target_dir.mkdir(parents=True, exist_ok=True)

    cfg = HeliaConfig(
        bootstrap=list(bootstrap or DEFAULT_HELIA_BOOTSTRAP),
        public_gateways=list(public_gateways or DEFAULT_PUBLIC_GATEWAYS),
    )
    out_path = target_dir / filename
    out_path.write_text(cfg.to_json(), encoding="utf-8")
    return out_path


def generate_pwa_helia_ts_stub() -> str:
    """生成 PWA helia.ts stub 代码 (供前端实现 IPFS 调用).

    Returns:
        TS 源码 string (caller 自行写到 pwa/src/ipfs/helia.ts).
    """
    return """\
// pwa/src/ipfs/helia.ts (sisoul Wave A-3 generated stub)
// PWA 内嵌 IPFS, 通过 helia 浏览器 SDK.
//
// 依赖:
//   npm i helia @helia/unixfs @libp2p/webtransport @libp2p/webrtc @libp2p/websockets
//
// 用法:
//   const node = await getHelia();
//   const cid = await ipfsAdd(node, new TextEncoder().encode("hello"));
//   const data = await ipfsCat(node, cid);

import { createHelia, type Helia } from 'helia';
import { unixfs } from '@helia/unixfs';
import { CID } from 'multiformats/cid';

let _node: Helia | null = null;

interface SisoulHeliaConfig {
    bootstrap: string[];
    public_gateways: string[];
    transports: string[];
    dht_enabled: boolean;
    dht_client_mode: boolean;
    blockstore: string;
    datastore: string;
}

async function loadConfig(): Promise<SisoulHeliaConfig> {
    const resp = await fetch('/ipfs/helia-config.json');
    if (!resp.ok) throw new Error('helia-config.json missing');
    return await resp.json();
}

export async function getHelia(): Promise<Helia> {
    if (_node) return _node;
    const cfg = await loadConfig();
    _node = await createHelia({
        // helia 默认含 webtransport + webrtc, 这里只覆盖 bootstrap
        libp2p: {
            peerDiscovery: cfg.bootstrap.length > 0 ? undefined : undefined,
            // ... 真实配置详 helia 文档
        },
    });
    return _node;
}

export async function ipfsAdd(node: Helia, data: Uint8Array): Promise<string> {
    const fs = unixfs(node);
    const cid = await fs.addBytes(data);
    return cid.toString();
}

export async function ipfsCat(node: Helia, cidStr: string): Promise<Uint8Array> {
    const fs = unixfs(node);
    const cid = CID.parse(cidStr);
    const chunks: Uint8Array[] = [];
    for await (const chunk of fs.cat(cid)) chunks.push(chunk);
    const total = chunks.reduce((s, c) => s + c.byteLength, 0);
    const out = new Uint8Array(total);
    let off = 0;
    for (const c of chunks) { out.set(c, off); off += c.byteLength; }
    return out;
}

export async function ipfsCatViaGateway(cidStr: string): Promise<Uint8Array> {
    // 浏览器 helia 拉不到时回退 HTTP gateway
    const cfg = await loadConfig();
    let lastErr: unknown = null;
    for (const tpl of cfg.public_gateways) {
        const url = tpl.replace('{cid}', cidStr);
        try {
            const resp = await fetch(url);
            if (resp.ok) return new Uint8Array(await resp.arrayBuffer());
            lastErr = new Error(`${url} ${resp.status}`);
        } catch (e) { lastErr = e; }
    }
    throw new Error(`ipfsCatViaGateway: all gateways failed (${lastErr})`);
}
"""


def write_pwa_helia_ts_stub(target_path: Path) -> Path:
    """把 TS stub 写到 pwa/src/ipfs/helia.ts.

    Args:
        target_path: 目标 TS 文件路径.

    Returns:
        写好的路径.
    """
    target_path = Path(target_path).expanduser()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(generate_pwa_helia_ts_stub(), encoding="utf-8")
    return target_path


__all__ = [
    "DEFAULT_HELIA_BOOTSTRAP",
    "DEFAULT_PUBLIC_GATEWAYS",
    "HeliaConfig",
    "HeliaTransport",
    "write_pwa_helia_config",
    "generate_pwa_helia_ts_stub",
    "write_pwa_helia_ts_stub",
]
