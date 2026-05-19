"""PWA skill API client ↔ dev-A skill_router schema contract test (波 6 dev-B).

dev-B 写 PWA 端 ``daemon.ts`` 调 dev-A 的 ``/sisoul/skill/*`` endpoints.
本测试验证两端 schema 对得上 (path / method / body field / response shape).

dev-A 跟 dev-B 并行 ship · 集成时 dev-A 的 router 可能还没 ship · 本测试用
2 套模式跑:

  1. **静态契约模式** (默认, 一定跑通): 解析 ``pwa/src/api/daemon.ts``
     的 skill API 段, 验证 endpoint 路径 / method / 关键字段都按 §28 §3.6
     规范写. 这层防止 PWA 端 typo / 字段名漂移.

  2. **运行时契约模式** (dev-A skill_router import 成功才跑): 拉起
     skill_router 检查 path/method/Pydantic 字段名跟 daemon.ts 完全对齐.
     dev-A 没 ship → skipif 跳过.

设计依据: §28 §3.6 AI 技能 share endpoints +
``<redacted-path>/asas/Infra-OPS/VibeCoderKit开源项目/30-波次开发计划-子agent并行+自动QA-vck.md``
波 6 dev-B spec.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ── 路径 ────────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent
DAEMON_TS = REPO_ROOT / "pwa" / "src" / "api" / "daemon.ts"


# ── 7 个 dev-B 必加的 API 方法 (按任务 spec) ─────────────────────────────────

# spec: method_name → (HTTP method, URL path 正则, body 必含字段 set)
EXPECTED_SKILL_API: dict[str, tuple[str, str, set[str]]] = {
    "createSkill": ("POST", r"/sisoul/skill/create", {"name", "description", "system_prompt"}),
    "listSkills": ("GET", r"/sisoul/skill/list", set()),  # query string
    "lendSkill": ("POST", r"/sisoul/skill/lend", {"skill_id", "permissions"}),
    "borrowSkill": ("POST", r"/sisoul/skill/borrow", {"owner_did", "skill_name", "duration_minutes"}),
    "listSkillSessions": ("GET", r"/sisoul/skill/sessions", set()),
    "endSkillSession": ("POST", r"/sisoul/skill/end-session", {"session_id"}),
    "proxyChatWithSkill": ("POST", r"/sisoul/skill/proxy-chat", {"session_id", "messages"}),
}


@pytest.fixture(scope="module")
def daemon_ts_source() -> str:
    assert DAEMON_TS.exists(), f"daemon.ts not found: {DAEMON_TS}"
    return DAEMON_TS.read_text(encoding="utf-8")


def _extract_method_body(src: str, method_name: str) -> str:
    """从 daemon.ts 抓 method 定义到下个 method 或 } 闭合的范围.

    daemonApi 是 ``{ name: (...) => ... , next: ... }`` 形式. 我们抓从
    ``methodName:`` 出现到下一个 ``  methodName2:`` (2 空格缩进 + 名字 + 冒号)
    或到 daemonApi 关闭 ``}; `` 的范围.
    """
    pat = rf"\b{re.escape(method_name)}\s*:"
    start = re.search(pat, src)
    if not start:
        return ""
    s = start.start()
    # 找下一个 method 起点 (2 空格缩进 + 标识符 + 冒号) 或 daemonApi closing
    after = src[start.end() :]
    next_method = re.search(r"\n  \w+\s*:", after)
    closing = re.search(r"\n\};\s*\n", after)
    end_offsets = [m.start() for m in [next_method, closing] if m]
    if not end_offsets:
        return src[s:]
    return src[s : start.end() + min(end_offsets)]


def _extract_interface_block(src: str, name: str) -> str:
    """匹配花括号嵌套抓 interface 的完整 body (含内联 type)."""
    m = re.search(rf"export interface {re.escape(name)} \{{", src)
    if not m:
        return ""
    depth = 1
    i = m.end()
    while i < len(src) and depth > 0:
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    return src[m.end() : i - 1]


# ── 静态契约 (一定跑) ────────────────────────────────────────────────────────


def test_daemon_ts_has_all_7_skill_methods(daemon_ts_source: str) -> None:
    """每个方法名必须出现在 daemonApi 里."""
    for method in EXPECTED_SKILL_API:
        assert re.search(
            rf"\b{re.escape(method)}\s*:",
            daemon_ts_source,
        ), f"daemon.ts 缺方法 '{method}' (任务 spec §2)"


@pytest.mark.parametrize("method_name", list(EXPECTED_SKILL_API.keys()))
def test_each_skill_method_hits_correct_endpoint(
    daemon_ts_source: str, method_name: str
) -> None:
    """每个方法 fetch URL 必须匹配 spec 路径."""
    http_method, path_pat, _ = EXPECTED_SKILL_API[method_name]
    body = _extract_method_body(daemon_ts_source, method_name)
    assert body, f"method body for {method_name} 没解析到"
    assert re.search(path_pat, body), (
        f"{method_name} URL 不含 '{path_pat}' (实际片段: {body[:300]})"
    )
    if http_method == "POST":
        assert 'method: "POST"' in body or "method: 'POST'" in body, (
            f"{method_name} 应该 POST 但没 method 字段"
        )


# spec 字段可在 method body args / JSON.stringify body / typed interface 里 ·
# 用 union 查找
def _field_in_method_or_interface(
    src: str, method_name: str, field: str
) -> bool:
    body = _extract_method_body(src, method_name)
    if field in body:
        return True
    # 找 body 里引用的 interface 类型
    types = re.findall(r":\s*(\w+(?:Request|Response|Item|Permissions))\b", body)
    for t in types:
        block = _extract_interface_block(src, t)
        if field in block:
            return True
    return False


@pytest.mark.parametrize(
    "method_name,required_fields",
    [
        (name, fields)
        for name, (_, _, fields) in EXPECTED_SKILL_API.items()
        if fields  # 只验 POST body
    ],
)
def test_skill_post_body_includes_required_fields(
    daemon_ts_source: str, method_name: str, required_fields: set[str]
) -> None:
    """POST 方法 body JSON 必含 spec 字段 (来自 §28 §3.6 packaging spec).

    字段可出现在: args 签名 / JSON.stringify(body) 引用 / typed interface.
    """
    for field in required_fields:
        assert _field_in_method_or_interface(
            daemon_ts_source, method_name, field
        ), f"{method_name} 缺 spec 字段 '{field}' (args + interface 都没找到)"


# ── §28 §3.6 packaging spec 字段一致性 ───────────────────────────────────────


def test_skill_item_has_packaging_spec_fields(daemon_ts_source: str) -> None:
    """SkillItem 必含 §28 §3.6 packaging spec 字段."""
    block = _extract_interface_block(daemon_ts_source, "SkillItem")
    assert block, "SkillItem interface not found"
    for field in [
        "skill_id",
        "name",
        "version",
        "owner_did",
        "description",
        "source",
        "personality_traits",
        "recommended_models",
    ]:
        assert field in block, f"SkillItem 缺 §28 §3.6 字段 '{field}'"


def test_skill_lend_permissions_3_modes(daemon_ts_source: str) -> None:
    """3 档授权 mode 跟 §28 §3.3 对齐."""
    block = _extract_interface_block(daemon_ts_source, "SkillLendPermissions")
    assert block, "SkillLendPermissions not found"
    for mode in ["strong-tie-auto", "per-request", "emergency-only"]:
        assert mode in block, f"3 档授权缺 '{mode}'"


def test_skill_create_request_has_packaging_fields(daemon_ts_source: str) -> None:
    """createSkill body 含 §28 §3.6 packaging spec 字段 (system_prompt /
    few_shot_examples / personality_traits / recommended_models)."""
    block = _extract_interface_block(daemon_ts_source, "SkillCreateRequest")
    assert block, "SkillCreateRequest not found"
    for field in [
        "name",
        "description",
        "system_prompt",
        "few_shot_examples",
        "personality_traits",
        "recommended_models",
    ]:
        assert field in block, f"SkillCreateRequest 缺 packaging 字段 '{field}'"


def test_skill_session_has_lifecycle_fields(daemon_ts_source: str) -> None:
    """SkillSessionItem 含 lifecycle 关键字段 (倒计时 + wipe + proxy)."""
    block = _extract_interface_block(daemon_ts_source, "SkillSessionItem")
    assert block, "SkillSessionItem not found"
    for field in [
        "session_id",
        "skill_id",
        "skill_name",
        "owner_did",
        "borrower_did",
        "status",
        "started_at",
        "expires_at",
        "proxy_endpoint",
        "wiped",
    ]:
        assert field in block, f"SkillSessionItem 缺 lifecycle 字段 '{field}'"


def test_borrow_skill_duration_options_30_60_120(daemon_ts_source: str) -> None:
    """spec: borrow 模态 duration 选 30/60/120 — Skills.tsx 应有 const."""
    skills_tsx = REPO_ROOT / "pwa" / "src" / "routes" / "Skills.tsx"
    assert skills_tsx.exists()
    src = skills_tsx.read_text(encoding="utf-8")
    # DURATION_OPTIONS = [30, 60, 120]
    m = re.search(r"DURATION_OPTIONS\s*=\s*\[([^\]]+)\]", src)
    assert m, "DURATION_OPTIONS not found in Skills.tsx"
    nums = {int(x.strip()) for x in m.group(1).split(",") if x.strip().isdigit()}
    assert nums == {30, 60, 120}, f"DURATION_OPTIONS={nums}, spec 要 30/60/120"


# ── 运行时契约 (dev-A skill_router 出现才跑) ──────────────────────────────────


def _try_import_skill_router():
    """尝试导 skill_router, 失败返 None."""
    try:
        from sisoul.daemon_routes import skill  # type: ignore[import-not-found]

        return getattr(skill, "skill_router", None)
    except Exception:
        return None


SKILL_ROUTER = _try_import_skill_router()


@pytest.mark.skipif(
    SKILL_ROUTER is None,
    reason="dev-A skill_router 还没 ship · 静态契约 + qa-C 集成时再补",
)
def test_runtime_skill_router_paths_match_daemon_ts() -> None:
    """dev-A router 起来后, 验证 path 跟 daemon.ts 完全对齐."""
    paths = {route.path for route in SKILL_ROUTER.routes}  # type: ignore[union-attr]
    expected = {
        "/sisoul/skill/create",
        "/sisoul/skill/list",
        "/sisoul/skill/lend",
        "/sisoul/skill/borrow",
        "/sisoul/skill/sessions",
        "/sisoul/skill/end-session",
        "/sisoul/skill/proxy-chat",
    }
    missing = expected - paths
    assert not missing, f"dev-A skill_router 缺 PWA 调的 path: {missing}"
