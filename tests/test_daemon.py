"""Phase 1 W2 daemon HTTP API 测试."""

from __future__ import annotations

from fastapi.testclient import TestClient

from sisoul import __version__, DAEMON_PORT
from sisoul.daemon import create_app

client = TestClient(create_app())


def test_health_endpoint() -> None:
    """GET /sisoul/health 返 200 + 正确字段."""
    r = client.get("/sisoul/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert "phase" in body
    assert body["daemon"]["port"] == DAEMON_PORT
    assert "/sisoul/health" in body["daemon"]["endpoints_implemented"]


def test_health_endpoint_lists_planned_endpoints() -> None:
    """health 响应含 planned endpoints 清单 (给后续 phase 提示)."""
    r = client.get("/sisoul/health")
    body = r.json()
    planned = body["daemon"]["endpoints_planned"]
    assert len(planned) >= 10
    for e in ["/sisoul/preferences", "/sisoul/audit", "/sisoul/borrow"]:
        assert e in planned


def test_unknown_endpoint_returns_404() -> None:
    """未实现 endpoint 返 404 (反向验证)."""
    r = client.get("/sisoul/nonexistent")
    assert r.status_code == 404


def test_daemon_port_not_conflicting_with_known_services() -> None:
    """sanity check: daemon 端口跟已知服务不冲突 (9890 backup / 9878 panshi-pro-bt / 9888 mac-jobs / 9892-9893 vck-supervisor)."""
    known_taken = {9890, 9878, 9888, 9892, 9893, 7890}
    assert DAEMON_PORT not in known_taken, f"端口 {DAEMON_PORT} 跟已知服务冲突"
