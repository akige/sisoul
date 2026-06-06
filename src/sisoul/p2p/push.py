"""sisoul p2p · push notifications + 在线状态 (Wave B' P1-1 · agent-B3).

§31 #14 推送 · §36 §4 P1-1 真做:
    - 订阅 Waku content topic ``/sisoul/{did}_inbox/v1/notify``, 收 borrow request /
      lend response → 推 daemon log + PWA WebSocket + macOS Notification (osascript).
    - 朋友离线侦测: heartbeat ``/sisoul/peer/heartbeat`` 每 60s, 超 5min 标 offline.
    - 离线 borrow request 排队 Waku store-and-forward (依赖 agent-B1 transport), 在线
      后 catchup.

⚠️ **agent-B1 Waku transport 接口未 ship**. 本模块顶部定义 stub interface
``WakuTransport`` ABC + ``InMemoryWakuTransport`` mock impl (pytest 默认走它).
主会话集成时换真 ``sisoul.p2p.waku_transport.WakuTransport``.

⚠️ **不在 mac/remote-vps 测试/部署**. 本模块只 mock + pytest. macOS Notification 默认
   noop (set env ``SISOUL_NOTIFY_OSASCRIPT=1`` 才真调 osascript, 测试时永远关).

模块结构:
    - ``WakuTransportProtocol`` (typing.Protocol): subscribe_topic / query_store /
      send / unsubscribe / get_peer_id 接口 (agent-B1 约定)
    - ``InMemoryWakuTransport``: pytest 默认 mock impl
    - ``Notification`` dataclass: 单条推送 (kind / source_did / payload / ts)
    - ``NotificationStore``: SQLite 持久化 (~/.sisoul/push/notifications.db)
    - ``HeartbeatTracker``: peer did → last_seen ts 跟踪, 标 online/offline
    - ``PushService``: 单例编排
        - register_listener(callback): WebSocket / CLI / 日志多消费者订阅
        - on_message(topic, payload): Waku 收到消息回调
        - send_borrow_notify(friend_did, request): 推 friend (在线发, 离线 store)
        - heartbeat_loop(): 后台 60s 一次发自己 heartbeat
        - offline_sweep(): 后台 60s 一次扫超 5min 没心跳的标 offline
        - catchup_for_did(my_did): peer 上线时 query Waku store 拿 inbox
    - 顶层模块函数: notify_friend / get_peer_status / list_recent / 给 borrow.py 用

线程模型: ``PushService`` 用 asyncio. CLI / daemon 调入口走 sync wrapper
``asyncio.run`` 或 daemon 的 event loop. 内部状态用 ``asyncio.Lock`` 保护.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import (
    Any,
    Awaitable,
    Callable,
    Iterable,
    Literal,
    Optional,
    Protocol,
    runtime_checkable,
)

log = logging.getLogger(__name__)


# ── Waku transport stub interface (agent-B1 约定) ─────────────────────────────

NotifyKind = Literal[
    "borrow_request",
    "borrow_approved",
    "borrow_denied",
    "lend_response",
    "peer_online",
    "peer_offline",
    "heartbeat",
    "system",
]


@dataclass
class WakuMessage:
    """Waku v2 message wire format (stub, agent-B1 集成时换真版)."""

    topic: str
    payload: dict[str, Any]
    ts: float = field(default_factory=time.time)
    msg_id: str = field(default_factory=lambda: "wm_" + uuid.uuid4().hex[:12])
    sender_peer_id: Optional[str] = None  # libp2p PeerID (agent-B1 填)


@runtime_checkable
class WakuTransportProtocol(Protocol):
    """agent-B1 Waku transport 接口约定 (本 push.py 唯一依赖).

    集成时换 ``sisoul.p2p.waku_transport.WakuTransport``. 接口签名固定,
    agent-B1 必须遵循 (跨 agent 约定).
    """

    async def subscribe_topic(
        self, topic: str, callback: Callable[[WakuMessage], Awaitable[None]]
    ) -> None: ...

    async def unsubscribe(self, topic: str) -> None: ...

    async def send(self, topic: str, payload: dict[str, Any]) -> WakuMessage: ...

    async def query_store(
        self, topic: str, since_ts: float = 0.0
    ) -> list[WakuMessage]:
        """store-and-forward: 拉 topic 上 since_ts 之后的历史 msg."""
        ...

    async def get_peer_id(self) -> str: ...


# ── InMemory Waku transport (pytest 默认 mock) ───────────────────────────────


class InMemoryWakuTransport:
    """单进程 in-memory Waku mock — pytest 默认走它.

    特性:
        - 多 subscriber 同 topic 都收 (fan-out).
        - ``send`` 同时 (a) 落入 store, (b) 投递所有 subscriber.
        - ``query_store`` 返 store 内全部 (since_ts 过滤).
        - 跨 transport 实例不共享 — 一个测试用例一个新 transport, OR 用 ``shared_store``
          字典让多实例共享 (模拟跨 peer 发现).

    集成 agent-B1 时用 nwaku / go-waku binary subprocess 替换.
    """

    def __init__(
        self,
        peer_id: Optional[str] = None,
        shared_store: Optional[dict[str, list[WakuMessage]]] = None,
    ) -> None:
        self._peer_id = peer_id or "inmem_" + uuid.uuid4().hex[:10]
        # topic -> list[callback]
        self._subscribers: dict[
            str, list[Callable[[WakuMessage], Awaitable[None]]]
        ] = {}
        # topic -> list[message] (store-and-forward)
        # 用 shared_store 可让多个 InMemoryWakuTransport 实例共享 store
        # (模拟跨 peer 推送, 一个 send 在所有 peer 的 store 里都能 query 到)
        self._store: dict[str, list[WakuMessage]] = (
            shared_store if shared_store is not None else {}
        )
        self._lock = asyncio.Lock()

    async def get_peer_id(self) -> str:
        return self._peer_id

    async def subscribe_topic(
        self, topic: str, callback: Callable[[WakuMessage], Awaitable[None]]
    ) -> None:
        async with self._lock:
            self._subscribers.setdefault(topic, []).append(callback)

    async def unsubscribe(self, topic: str) -> None:
        async with self._lock:
            self._subscribers.pop(topic, None)

    async def send(self, topic: str, payload: dict[str, Any]) -> WakuMessage:
        msg = WakuMessage(
            topic=topic, payload=payload, sender_peer_id=self._peer_id
        )
        # 1. 落 store
        async with self._lock:
            self._store.setdefault(topic, []).append(msg)
            subs = list(self._subscribers.get(topic, []))
        # 2. 投递 subscriber (lock 外, 避免回调里调 send 死锁)
        for cb in subs:
            try:
                await cb(msg)
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "InMemoryWakuTransport callback raised on topic %s: %s",
                    topic,
                    e,
                )
        return msg

    async def query_store(
        self, topic: str, since_ts: float = 0.0
    ) -> list[WakuMessage]:
        async with self._lock:
            msgs = list(self._store.get(topic, []))
        return [m for m in msgs if m.ts > since_ts]

    # ── 测试辅助 ──────────────────────────────────────────────────────────────

    def _clear_store(self) -> None:
        """测试用: 清 store + subscriber."""
        self._store.clear()
        self._subscribers.clear()


# ── 数据 ───────────────────────────────────────────────────────────────────────


@dataclass
class Notification:
    """单条 push notification (PWA / CLI / log 共用)."""

    notify_id: str
    kind: NotifyKind
    source_did: str  # 发起方 DID (谁发的; 自己心跳=self_did)
    target_did: str  # 接收方 DID (通常 = self)
    payload: dict[str, Any]
    ts: float
    read: bool = False
    delivered_via: list[str] = field(default_factory=list)  # ["log","ws","macos"]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


PeerState = Literal["online", "offline", "unknown"]


@dataclass
class PeerStatus:
    """朋友 online/offline 状态."""

    did: str
    state: PeerState
    last_heartbeat_ts: Optional[float]
    last_seen_age_sec: Optional[float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── 持久化 ──────────────────────────────────────────────────────────────────────


_DEFAULT_NOTIFY_DB = Path.home() / ".sisoul" / "push" / "notifications.db"


def _ensure_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(path)) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS notifications (
                notify_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                source_did TEXT NOT NULL,
                target_did TEXT NOT NULL,
                payload TEXT NOT NULL,
                ts REAL NOT NULL,
                read INTEGER NOT NULL DEFAULT 0,
                delivered_via TEXT NOT NULL DEFAULT '[]'
            );
            CREATE INDEX IF NOT EXISTS idx_notify_ts ON notifications(ts DESC);
            CREATE INDEX IF NOT EXISTS idx_notify_target ON notifications(target_did, ts DESC);
            CREATE TABLE IF NOT EXISTS heartbeats (
                did TEXT PRIMARY KEY,
                last_heartbeat_ts REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS catchup_cursors (
                topic TEXT PRIMARY KEY,
                last_ts REAL NOT NULL
            );
            """
        )
        conn.commit()


class NotificationStore:
    """SQLite 持久化 (~/.sisoul/push/notifications.db).

    线程安全: 每调用一次开/关 connection (SQLite ``connect`` 自带 file-level lock).
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = Path(db_path) if db_path else _DEFAULT_NOTIFY_DB
        _ensure_db(self.db_path)

    def insert(self, n: Notification) -> None:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO notifications "
                "(notify_id, kind, source_did, target_did, payload, ts, read, delivered_via) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    n.notify_id,
                    n.kind,
                    n.source_did,
                    n.target_did,
                    json.dumps(n.payload),
                    n.ts,
                    1 if n.read else 0,
                    json.dumps(n.delivered_via),
                ),
            )
            conn.commit()

    def list_recent(
        self,
        limit: int = 50,
        target_did: Optional[str] = None,
        kind: Optional[NotifyKind] = None,
        unread_only: bool = False,
    ) -> list[Notification]:
        sql = "SELECT notify_id, kind, source_did, target_did, payload, ts, read, delivered_via FROM notifications WHERE 1=1"
        params: list[Any] = []
        if target_did:
            sql += " AND target_did=?"
            params.append(target_did)
        if kind:
            sql += " AND kind=?"
            params.append(kind)
        if unread_only:
            sql += " AND read=0"
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(int(limit))
        out: list[Notification] = []
        with sqlite3.connect(str(self.db_path)) as conn:
            for row in conn.execute(sql, params):
                out.append(
                    Notification(
                        notify_id=row[0],
                        kind=row[1],
                        source_did=row[2],
                        target_did=row[3],
                        payload=json.loads(row[4]),
                        ts=row[5],
                        read=bool(row[6]),
                        delivered_via=json.loads(row[7]),
                    )
                )
        return out

    def mark_read(self, notify_id: str) -> bool:
        with sqlite3.connect(str(self.db_path)) as conn:
            cur = conn.execute(
                "UPDATE notifications SET read=1 WHERE notify_id=?", (notify_id,)
            )
            conn.commit()
            return cur.rowcount > 0

    def upsert_heartbeat(self, did: str, ts: float) -> None:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO heartbeats(did,last_heartbeat_ts) VALUES(?,?) "
                "ON CONFLICT(did) DO UPDATE SET last_heartbeat_ts=excluded.last_heartbeat_ts",
                (did, ts),
            )
            conn.commit()

    def get_heartbeat(self, did: str) -> Optional[float]:
        with sqlite3.connect(str(self.db_path)) as conn:
            cur = conn.execute(
                "SELECT last_heartbeat_ts FROM heartbeats WHERE did=?", (did,)
            )
            row = cur.fetchone()
            return float(row[0]) if row else None

    def list_heartbeats(self) -> dict[str, float]:
        with sqlite3.connect(str(self.db_path)) as conn:
            return {
                row[0]: float(row[1])
                for row in conn.execute(
                    "SELECT did, last_heartbeat_ts FROM heartbeats"
                )
            }

    def get_catchup_cursor(self, topic: str) -> float:
        with sqlite3.connect(str(self.db_path)) as conn:
            cur = conn.execute(
                "SELECT last_ts FROM catchup_cursors WHERE topic=?", (topic,)
            )
            row = cur.fetchone()
            return float(row[0]) if row else 0.0

    def set_catchup_cursor(self, topic: str, ts: float) -> None:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO catchup_cursors(topic,last_ts) VALUES(?,?) "
                "ON CONFLICT(topic) DO UPDATE SET last_ts=excluded.last_ts",
                (topic, ts),
            )
            conn.commit()


# ── topic 构造 ─────────────────────────────────────────────────────────────────


def inbox_topic(did: str) -> str:
    """私人 inbox topic: ``/sisoul/{did}_inbox/v1/notify``.

    朋友发推送给我 = send 到 ``inbox_topic(my_did)``.
    """
    # 简单清理: Waku content topic 不许 / 之外的特殊字符可控; did:key:zXXX 直接用
    safe = did.replace("/", "_")
    return f"/sisoul/{safe}_inbox/v1/notify"


HEARTBEAT_TOPIC = "/sisoul/peer/heartbeat/v1"


# ── 心跳跟踪 ────────────────────────────────────────────────────────────────────


HEARTBEAT_INTERVAL_SEC = 60
OFFLINE_THRESHOLD_SEC = 300  # 5 min


class HeartbeatTracker:
    """peer DID → last_seen ts. 超 ``OFFLINE_THRESHOLD_SEC`` 标 offline."""

    def __init__(
        self,
        store: Optional[NotificationStore] = None,
        offline_threshold_sec: float = OFFLINE_THRESHOLD_SEC,
    ) -> None:
        self._store = store or NotificationStore()
        self.offline_threshold_sec = offline_threshold_sec

    def record(self, did: str, ts: Optional[float] = None) -> None:
        self._store.upsert_heartbeat(did, ts if ts is not None else time.time())

    def get_status(self, did: str, now: Optional[float] = None) -> PeerStatus:
        now = now if now is not None else time.time()
        last = self._store.get_heartbeat(did)
        if last is None:
            return PeerStatus(
                did=did, state="unknown", last_heartbeat_ts=None, last_seen_age_sec=None
            )
        age = now - last
        state: PeerState = "online" if age <= self.offline_threshold_sec else "offline"
        return PeerStatus(
            did=did, state=state, last_heartbeat_ts=last, last_seen_age_sec=age
        )

    def list_all(self, now: Optional[float] = None) -> list[PeerStatus]:
        now = now if now is not None else time.time()
        out: list[PeerStatus] = []
        for did, last in self._store.list_heartbeats().items():
            age = now - last
            state: PeerState = (
                "online" if age <= self.offline_threshold_sec else "offline"
            )
            out.append(
                PeerStatus(
                    did=did, state=state, last_heartbeat_ts=last, last_seen_age_sec=age
                )
            )
        return out


# ── macOS Notification (osascript) ─────────────────────────────────────────────


def _macos_notify_osascript(title: str, message: str) -> bool:
    """macOS desktop notification (osascript display notification).

    默认 noop. ``SISOUL_NOTIFY_OSASCRIPT=1`` 才真调. 测试时永远关 (用户约束:
    "不在 mac 测试/部署").
    """
    if os.environ.get("SISOUL_NOTIFY_OSASCRIPT") != "1":
        log.debug("macos notify (noop): %s | %s", title, message)
        return False
    try:
        import subprocess

        # 转义双引号 + 反斜杠
        safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
        safe_message = message.replace("\\", "\\\\").replace('"', '\\"')
        script = f'display notification "{safe_message}" with title "{safe_title}"'
        subprocess.run(
            ["osascript", "-e", script],
            check=False,
            timeout=5,
            capture_output=True,
        )
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("macos osascript notify failed: %s", e)
        return False


# 测试可注入 mock:
_INJECTED_MACOS_NOTIFY: Optional[Callable[[str, str], bool]] = None


def set_mock_macos_notify(fn: Optional[Callable[[str, str], bool]]) -> None:
    """pytest 注入 mock 替 osascript 调用 (默认 None 走真 osascript wrapper)."""
    global _INJECTED_MACOS_NOTIFY
    _INJECTED_MACOS_NOTIFY = fn


def _dispatch_macos(title: str, message: str) -> bool:
    if _INJECTED_MACOS_NOTIFY is not None:
        try:
            return bool(_INJECTED_MACOS_NOTIFY(title, message))
        except Exception as e:  # noqa: BLE001
            log.warning("injected macos notify raised: %s", e)
            return False
    return _macos_notify_osascript(title, message)


# ── PushService (主编排单例) ───────────────────────────────────────────────────


# WebSocket / CLI 订阅者回调 (异步)
NotifyListener = Callable[[Notification], Awaitable[None]]


class PushService:
    """单例编排. daemon 启动时 ``create_push_service(self_did, transport)``.

    职责:
        1. 订阅 ``inbox_topic(self_did)`` + ``HEARTBEAT_TOPIC``.
        2. 收到 message → 转 ``Notification`` → 持久化 → fan-out 给 listener (PWA WS) +
           macOS desktop notify + log.
        3. ``notify_friend(friend_did, kind, payload)`` 发推送 (在线发, 离线 store 排队).
        4. heartbeat_loop: 每 60s 发自己心跳到 ``HEARTBEAT_TOPIC``.
        5. offline_sweep: 每 60s 扫超 5min 没心跳的 peer, 状态查询时反映.
        6. ``catchup_for_did(my_did)`` peer 上线 / daemon 重启时 query store 拉 inbox.
    """

    def __init__(
        self,
        self_did: str,
        transport: WakuTransportProtocol,
        store: Optional[NotificationStore] = None,
        tracker: Optional[HeartbeatTracker] = None,
        heartbeat_interval_sec: float = HEARTBEAT_INTERVAL_SEC,
        offline_threshold_sec: float = OFFLINE_THRESHOLD_SEC,
    ) -> None:
        self.self_did = self_did
        self.transport = transport
        self.store = store or NotificationStore()
        self.tracker = tracker or HeartbeatTracker(
            self.store, offline_threshold_sec=offline_threshold_sec
        )
        self.heartbeat_interval_sec = heartbeat_interval_sec
        self.offline_threshold_sec = offline_threshold_sec
        self._listeners: list[NotifyListener] = []
        self._listeners_lock = asyncio.Lock()
        self._started = False
        self._stop_event = asyncio.Event()
        self._tasks: list[asyncio.Task[Any]] = []

    # ── listener 注册 ──────────────────────────────────────────────────────────

    async def register_listener(self, cb: NotifyListener) -> Callable[[], Awaitable[None]]:
        """注册 (WebSocket / CLI) 返 unregister 异步函数."""
        async with self._listeners_lock:
            self._listeners.append(cb)

        async def _unreg() -> None:
            async with self._listeners_lock:
                try:
                    self._listeners.remove(cb)
                except ValueError:
                    pass

        return _unreg

    async def _fanout(self, n: Notification) -> None:
        async with self._listeners_lock:
            listeners = list(self._listeners)
        for cb in listeners:
            try:
                await cb(n)
            except Exception as e:  # noqa: BLE001
                log.warning("notify listener raised: %s", e)

    # ── 启停 ───────────────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._stop_event.clear()
        # 订阅自己的 inbox
        await self.transport.subscribe_topic(
            inbox_topic(self.self_did), self._on_inbox_message
        )
        # 订阅心跳广播
        await self.transport.subscribe_topic(
            HEARTBEAT_TOPIC, self._on_heartbeat_message
        )
        # 启 heartbeat_loop + offline_sweep 后台 task
        self._tasks.append(asyncio.create_task(self._heartbeat_loop()))
        self._tasks.append(asyncio.create_task(self._offline_sweep_loop()))
        log.info("PushService started for did=%s", self.self_did)

    async def stop(self) -> None:
        if not self._started:
            return
        self._stop_event.set()
        try:
            await self.transport.unsubscribe(inbox_topic(self.self_did))
        except Exception:
            pass
        try:
            await self.transport.unsubscribe(HEARTBEAT_TOPIC)
        except Exception:
            pass
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._tasks.clear()
        self._started = False
        log.info("PushService stopped")

    # ── 入站 message 处理 ──────────────────────────────────────────────────────

    async def _on_inbox_message(self, msg: WakuMessage) -> None:
        payload = msg.payload
        kind = str(payload.get("kind", "system"))
        if kind not in (
            "borrow_request",
            "borrow_approved",
            "borrow_denied",
            "lend_response",
            "peer_online",
            "peer_offline",
            "system",
        ):
            kind = "system"
        # 去重: 用 msg_id 当 notify_id 前缀
        notify_id = "n_" + msg.msg_id[3:] if msg.msg_id.startswith("wm_") else (
            "n_" + uuid.uuid4().hex[:12]
        )
        # 已存在则跳 (catchup 多次拉重复 msg)
        existing = self.store.list_recent(limit=200, target_did=self.self_did)
        if any(e.notify_id == notify_id for e in existing):
            log.debug("dup notification ignored: %s", notify_id)
            return
        source_did = str(payload.get("source_did", msg.sender_peer_id or "unknown"))
        n = Notification(
            notify_id=notify_id,
            kind=kind,  # type: ignore[arg-type]
            source_did=source_did,
            target_did=self.self_did,
            payload=payload,
            ts=msg.ts,
        )
        delivered = ["log"]
        log.info(
            "[notify] kind=%s source=%s payload=%s", kind, source_did, payload
        )
        # macOS notify
        title = f"sisoul · {kind}"
        message = self._format_macos_message(n)
        if _dispatch_macos(title, message):
            delivered.append("macos")
        # WebSocket / 其他 listener
        await self._fanout(n)
        if self._listeners:
            delivered.append("ws")
        n.delivered_via = delivered
        self.store.insert(n)
        # 更新 catchup cursor
        topic = inbox_topic(self.self_did)
        cursor = self.store.get_catchup_cursor(topic)
        if msg.ts > cursor:
            self.store.set_catchup_cursor(topic, msg.ts)

    async def _on_heartbeat_message(self, msg: WakuMessage) -> None:
        did = str(msg.payload.get("did", ""))
        if not did or did == self.self_did:
            return
        ts = float(msg.payload.get("ts", msg.ts))
        self.tracker.record(did, ts)
        log.debug("heartbeat recorded: did=%s ts=%s", did, ts)

    @staticmethod
    def _format_macos_message(n: Notification) -> str:
        p = n.payload
        if n.kind == "borrow_request":
            return f"{n.source_did[:16]}… 想借 {p.get('amount','?')} {p.get('resource_type','?')}"
        if n.kind == "borrow_approved":
            return f"{n.source_did[:16]}… 同意了你的借用请求"
        if n.kind == "borrow_denied":
            return f"{n.source_did[:16]}… 拒绝了你的借用请求"
        if n.kind == "lend_response":
            return f"{n.source_did[:16]}… 回复了你的借用请求"
        return f"{n.kind} from {n.source_did[:16]}…"

    # ── 出站 (notify_friend) ───────────────────────────────────────────────────

    async def notify_friend(
        self,
        friend_did: str,
        kind: NotifyKind,
        payload: dict[str, Any],
    ) -> WakuMessage:
        """发推送给朋友. 在线 → 实时投递 (transport 收到); 离线 → 落 Waku store, 朋友上线 catchup.

        store-and-forward 由 transport 实现 (``InMemoryWakuTransport`` 永远落 store).
        """
        full_payload = {
            "kind": kind,
            "source_did": self.self_did,
            "ts": time.time(),
            **payload,
        }
        topic = inbox_topic(friend_did)
        msg = await self.transport.send(topic, full_payload)
        log.info(
            "notify_friend sent: friend=%s kind=%s topic=%s msg_id=%s",
            friend_did,
            kind,
            topic,
            msg.msg_id,
        )
        return msg

    # ── heartbeat loop ─────────────────────────────────────────────────────────

    async def _heartbeat_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    await self.transport.send(
                        HEARTBEAT_TOPIC,
                        {"did": self.self_did, "ts": time.time()},
                    )
                except Exception as e:  # noqa: BLE001
                    log.warning("heartbeat send failed: %s", e)
                # 用 wait 让 stop_event 立即打断 sleep
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=self.heartbeat_interval_sec
                    )
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise

    async def _offline_sweep_loop(self) -> None:
        """每 60s 扫一次, 把刚跨过 offline 阈值的 peer 投一条 ``peer_offline`` notification."""
        prev_states: dict[str, PeerState] = {}
        try:
            while not self._stop_event.is_set():
                try:
                    for st in self.tracker.list_all():
                        prev = prev_states.get(st.did)
                        if prev and prev != st.state:
                            kind: NotifyKind = (
                                "peer_online" if st.state == "online" else "peer_offline"
                            )
                            n = Notification(
                                notify_id="n_" + uuid.uuid4().hex[:12],
                                kind=kind,
                                source_did=st.did,
                                target_did=self.self_did,
                                payload={
                                    "did": st.did,
                                    "state": st.state,
                                    "last_heartbeat_ts": st.last_heartbeat_ts,
                                },
                                ts=time.time(),
                                delivered_via=["log"],
                            )
                            self.store.insert(n)
                            await self._fanout(n)
                            log.info(
                                "peer state change: %s %s → %s", st.did, prev, st.state
                            )
                        prev_states[st.did] = st.state
                except Exception as e:  # noqa: BLE001
                    log.warning("offline_sweep raised: %s", e)
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=60.0)
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise

    # ── catchup ────────────────────────────────────────────────────────────────

    async def catchup(self) -> list[Notification]:
        """daemon 启动 / peer 上线时调: query Waku store 拉 inbox 漏的 msg, 投到 listener.

        用 ``catchup_cursors`` 跟踪 last_ts 避免重复.
        """
        topic = inbox_topic(self.self_did)
        cursor = self.store.get_catchup_cursor(topic)
        try:
            msgs = await self.transport.query_store(topic, since_ts=cursor)
        except Exception as e:  # noqa: BLE001
            log.warning("catchup query_store failed: %s", e)
            return []
        out: list[Notification] = []
        for m in msgs:
            # 跑入站处理 (会去重 + 持久化 + fan-out)
            await self._on_inbox_message(m)
            existing = self.store.list_recent(
                limit=1, target_did=self.self_did
            )
            if existing:
                out.append(existing[0])
        log.info("catchup: %d msg picked up since %s", len(msgs), cursor)
        return out


# ── 单例 + 顶层便捷函数 (给 borrow.py / cli / daemon 用) ──────────────────────


_SERVICE: Optional[PushService] = None
_SERVICE_LOCK = threading.Lock()


def get_push_service() -> Optional[PushService]:
    return _SERVICE


def set_push_service(svc: Optional[PushService]) -> None:
    """测试 / daemon 启动用."""
    global _SERVICE
    with _SERVICE_LOCK:
        _SERVICE = svc


def create_push_service(
    self_did: str,
    transport: Optional[WakuTransportProtocol] = None,
    store: Optional[NotificationStore] = None,
) -> PushService:
    """工厂: 默认用 InMemoryWakuTransport (mock). 主会话集成 agent-B1 时传真 transport."""
    if transport is None:
        transport = InMemoryWakuTransport(peer_id=self_did)
    svc = PushService(self_did=self_did, transport=transport, store=store)
    set_push_service(svc)
    return svc


# ── 顶层 sync wrapper (给 borrow.py 调) ──────────────────────────────────────


def notify_friend_sync(
    friend_did: str,
    kind: NotifyKind,
    payload: dict[str, Any],
) -> Optional[WakuMessage]:
    """给 borrow.py 等 sync 代码调: 自动跑在 daemon event loop OR asyncio.run 兜底.

    ``PushService`` 未初始化 (没 daemon) → noop 返 None, log warn.
    """
    svc = get_push_service()
    if svc is None:
        log.debug("notify_friend_sync: PushService 未初始化, noop")
        return None
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # 在 event loop 里 (FastAPI route) — 用 ensure_future fire-and-forget
            asyncio.ensure_future(svc.notify_friend(friend_did, kind, payload))
            return None
        else:
            return asyncio.run(svc.notify_friend(friend_did, kind, payload))
    except RuntimeError:
        return asyncio.run(svc.notify_friend(friend_did, kind, payload))


def get_peer_status(did: str) -> PeerStatus:
    """查朋友 online/offline. ``PushService`` 未初始化 → 用全局 default tracker."""
    svc = get_push_service()
    if svc is not None:
        return svc.tracker.get_status(did)
    return HeartbeatTracker().get_status(did)


def list_recent_notifications(
    limit: int = 50,
    target_did: Optional[str] = None,
    kind: Optional[NotifyKind] = None,
    unread_only: bool = False,
    store: Optional[NotificationStore] = None,
) -> list[Notification]:
    s = store or (
        get_push_service().store if get_push_service() else NotificationStore()
    )
    return s.list_recent(
        limit=limit, target_did=target_did, kind=kind, unread_only=unread_only
    )


def record_external_heartbeat(did: str, ts: Optional[float] = None) -> None:
    """daemon /sisoul/peer/heartbeat HTTP endpoint 调入口 (朋友 daemon 互发心跳).

    用法: 朋友 daemon POST 心跳 → daemon route 调本函数.
    """
    svc = get_push_service()
    if svc is not None:
        svc.tracker.record(did, ts)
    else:
        HeartbeatTracker().record(did, ts)


# ── 测试辅助 ──────────────────────────────────────────────────────────────────


def _reset_for_test() -> None:
    """tests only: 清单例 + injected mock."""
    set_push_service(None)
    set_mock_macos_notify(None)


__all__ = [
    # 类型
    "NotifyKind",
    "PeerState",
    "WakuMessage",
    "WakuTransportProtocol",
    "Notification",
    "PeerStatus",
    # impl
    "InMemoryWakuTransport",
    "NotificationStore",
    "HeartbeatTracker",
    "PushService",
    # topic
    "inbox_topic",
    "HEARTBEAT_TOPIC",
    "HEARTBEAT_INTERVAL_SEC",
    "OFFLINE_THRESHOLD_SEC",
    # 单例 + 工厂
    "get_push_service",
    "set_push_service",
    "create_push_service",
    # 顶层便捷
    "notify_friend_sync",
    "get_peer_status",
    "list_recent_notifications",
    "record_external_heartbeat",
    # macos notify mock
    "set_mock_macos_notify",
    # test only
    "_reset_for_test",
]
