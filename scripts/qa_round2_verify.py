"""M3 Round-2 独立回归脚本（QA 用）：C/D/E/G 组实测。

独立于 tests/unit/test_qa_stage3_active.py 再次实测关键链路，
验证工程师返工修复声明，不复述既有测试。
"""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from llm_platform.auth.api_key import generate_api_key
from llm_platform.db.repositories.key_repo import KeyRepo
from llm_platform.db.repositories.tenant_repo import TenantRepo
from llm_platform.db.session import SessionLocal, init_db
from llm_platform.eval.datasets import DatasetManager, compute_checksum
from llm_platform.eval.runner import EvalRunner
from llm_platform.errors import ValidationError
from llm_platform.main import create_app
from llm_platform.monitoring.alerts import AlertEngine
from llm_platform.monitoring.billing import BillingService
from llm_platform.routing.router import Router

PASS = []
FAIL = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append((name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def make_admin(client: TestClient):
    init_db()
    db = SessionLocal()
    try:
        uid = uuid.uuid4().hex[:8]
        tenant = TenantRepo(db).create_tenant(f"qa3r2-{uid}", monthly_budget=10000.0)
        project = TenantRepo(db).create_project(tenant.id, f"qa3r2p-{uid}")
        plaintext, key_hash, key_prefix = generate_api_key()
        KeyRepo(db).create_key(
            key_hash=key_hash,
            key_prefix=key_prefix,
            tenant_id=tenant.id,
            project_id=project.id,
            scopes=["chat", "admin"],
        )
        tid, pid = tenant.id, project.id
    finally:
        db.close()
    resp = client.post("/admin/v1/auth/token", json={"api_key": plaintext})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}, tid, pid


# ═══════════════ C. MCP 三端点 + 404 信封 ═══════════════
print("\n[C] MCP 三端点实测")
init_db()
app = create_app()
with TestClient(app) as client:
    headers, tid, pid = make_admin(client)

    # 注册
    r = client.post(
        "/admin/v1/mcp/tools",
        headers=headers,
        json={
            "name": f"qa-echo-{uuid.uuid4().hex[:6]}",
            "handler": "echo",
            "input_schema": {"type": "object"},
            "description": "QA round2 echo tool",
            "auth_type": "none",
        },
    )
    check("C1 注册工具 200", r.status_code == 200, f"status={r.status_code} body={r.text[:200]}")
    tool_name = r.json().get("name", "") if r.status_code == 200 else ""
    tool_id = r.json().get("id") if r.status_code == 200 else None
    schema_field = r.json().get("schema") if r.status_code == 200 else None
    check(
        "C1b McpToolOut 字段对齐（id/name/schema/auth_type/enabled）",
        r.status_code == 200
        and all(k in r.json() for k in ("id", "name", "schema", "auth_type", "enabled")),
        f"keys={list(r.json().keys()) if r.status_code == 200 else r.text[:150]}",
    )

    # 列表
    r2 = client.get("/admin/v1/mcp/tools", headers=headers)
    names = [t.get("name") for t in r2.json()] if r2.status_code == 200 else []
    check(
        "C2 列表包含新注册工具",
        r2.status_code == 200 and tool_name in names,
        f"status={r2.status_code} names={names[:5]}",
    )

    # 调用（handler=echo 的本地处理器）
    r3 = client.post(
        "/admin/v1/mcp/call",
        headers=headers,
        json={"tool_name": tool_name, "arguments": {"text": "hi"}},
    )
    ok3 = r3.status_code == 200 and "success" in r3.json()
    check("C3 调用工具 200 + success 字段", ok3, f"status={r3.status_code} body={r3.text[:200]}")

    # 未知工具 404 信封
    r4 = client.post(
        "/admin/v1/mcp/call",
        headers=headers,
        json={"tool_name": "no-such-tool-xyz", "arguments": {}},
    )
    env = r4.json().get("error", {}) if r4.status_code else {}
    check(
        "C4 未知工具 404 NOT_FOUND_006 统一信封",
        r4.status_code == 404 and env.get("code") == "NOT_FOUND_006",
        f"status={r4.status_code} code={env.get('code')} msg={str(env.get('message'))[:80]}",
    )
    check(
        "C4b 信封含 trace_id/context 键",
        r4.status_code == 404 and "trace_id" in env and "context" in env,
        f"env_keys={list(env.keys())}",
    )


# ═══════════════ D. checksum 双场景 ═══════════════
print("\n[D] checksum 双场景")
db = SessionLocal()
try:
    mgr = DatasetManager(db)

    # D1 无 metadata 键数据注册 → get_items 重算零误报
    ver = uuid.uuid4().hex[:8]
    raw_items = [
        {"input_prompt": f"问题{i}", "expected_output": f"答案{i}"} for i in range(5)
    ]  # 故意不带 metadata 键
    reg = mgr.register("qa3r2-nometa", f"1.0.0-{ver}", raw_items, source="qa-round2")
    items_back = mgr.get_items(reg["id"])
    ok_d1 = compute_checksum(items_back) == reg["checksum"]
    check(
        "D1 无 metadata 注册后 get_items 重算零误报",
        ok_d1,
        f"reg={reg['checksum'][:12]} recompute={compute_checksum(items_back)[:12]}",
    )
    check("D1b verify_checksum 通过", mgr.verify_checksum(reg["id"], items_back) is True)

    # D1c 带 metadata 键同样零误报
    meta_items = [
        {"input_prompt": f"q{i}", "expected_output": f"a{i}", "metadata": {"tag": "t"}} for i in range(3)
    ]
    reg2 = mgr.register("qa3r2-meta", f"1.0.0-{ver}-m", meta_items, source="qa-round2")
    ok_d1c = compute_checksum(mgr.get_items(reg2["id"])) == reg2["checksum"]
    check("D1c 带 metadata 注册后重算零误报", ok_d1c)

    # D2 篡改必拒（fail-closed）
    tampered = [dict(it) for it in mgr.get_items(reg["id"])]
    tampered[0]["expected_output"] = "被篡改的答案"
    runner = EvalRunner(db)
    try:
        runner._verify_dataset_checksum(reg["id"], tampered)
        check("D2 篡改数据 fail-closed 拒绝", False, "未抛异常（仍为告警模式）")
    except ValidationError as e:
        check("D2 篡改数据 fail-closed 拒绝（ValidationError）", True, f"msg={str(e)[:80]}")
    except Exception as e:  # noqa: BLE001
        check("D2 篡改数据 fail-closed 拒绝", False, f"抛出非 ValidationError: {type(e).__name__}: {e}")

    # D2b 未篡改不误拒
    try:
        runner._verify_dataset_checksum(reg["id"], mgr.get_items(reg["id"]))
        check("D2b 合法数据校验通过（不误拒）", True)
    except Exception as e:  # noqa: BLE001
        check("D2b 合法数据校验通过（不误拒）", False, f"{type(e).__name__}: {e}")
finally:
    db.close()


# ═══════════════ E. 灰度租户/Key 粒度 ═══════════════
print("\n[E] 灰度租户/Key 粒度抽验")
router = Router()

# E1 空名单 = 全量灰度兼容
router.set_canary(endpoint_ids=[2], weight=0.5)
ok_e1 = router._in_canary_scope(tenant_id=999) and router._in_canary_scope(key_hash="any")
check("E1 空白名单 = 全量灰度（兼容端点级）", ok_e1)

# E2 租户白名单命中/未命中
router.set_canary(endpoint_ids=[2], weight=1.0, tenant_ids=[10, 20])
ok_e2 = router._in_canary_scope(tenant_id=10) and not router._in_canary_scope(tenant_id=30)
check("E2 租户白名单命中/未命中正确", ok_e2)

# E3 Key 白名单命中/未命中
router.set_canary(endpoint_ids=[2], weight=1.0, key_hashes=["hash-a", "hash-b"])
ok_e3 = router._in_canary_scope(key_hash="hash-a") and not router._in_canary_scope(key_hash="hash-z")
check("E3 Key 白名单命中/未命中正确", ok_e3)

# E4 双白名单任一命中即可（tenant 命中、key 未命中）
router.set_canary(endpoint_ids=[2], weight=1.0, tenant_ids=[10], key_hashes=["hash-a"])
ok_e4 = router._in_canary_scope(tenant_id=10, key_hash="other") and router._in_canary_scope(
    tenant_id=99, key_hash="hash-a"
) and not router._in_canary_scope(tenant_id=99, key_hash="other")
check("E4 双白名单任一维度命中即可", ok_e4)

# E5 未命中白名单不参与灰度分流（weight=1.0 也走主）
import random

random.seed(42)
router.set_canary(endpoint_ids=[2], weight=1.0, tenant_ids=[10])
hits = sum(1 for _ in range(200) if router._should_route_canary(tenant_id=30))
check("E5 未命中白名单 200 次抽样 0 次进灰度", hits == 0, f"hits={hits}")
hits_ok = sum(1 for _ in range(200) if router._should_route_canary(tenant_id=10))
check("E5b 命中白名单 weight=1.0 时 200 次全进灰度", hits_ok == 200, f"hits={hits_ok}")

# E6 clear_canary 清空白名单
router.clear_canary()
check("E6 clear_canary 重置白名单", not router._canary_tenant_ids and not router._canary_key_hashes)


# ═══════════════ G. Round-1 通过项抽验（防回归）═══════════════
print("\n[G] Round-1 通过项抽验")

# G1 账单手算（随机 tenant 隔离历史数据，保证幂等）
db = SessionLocal()
try:
    billing_tenant = int(uuid.uuid4().hex[:8], 16) % 100000 + 500000
    billing = BillingService(db)
    # 插入确定性 call_log
    from datetime import datetime, timezone

    from llm_platform.models.entities import CallLog

    rows = []
    for i in range(4):
        rows.append(
            CallLog(
                tenant_id=billing_tenant,
                project_id=1,
                endpoint_id=1,
                trace_id=f"qa3r2-{i}",
                model="gpt-4o",
                prompt_tokens=100,
                completion_tokens=50,
                cost=0.01,
                latency_ms=120,
                status="ok",
                created_at=datetime(2025, 6, 1, 10, 0, i, tzinfo=timezone.utc),
            )
        )
    rows.append(
        CallLog(
            tenant_id=billing_tenant,
            project_id=1,
            endpoint_id=1,
            trace_id="qa3r2-err",
            model="gpt-4o",
            prompt_tokens=10,
            completion_tokens=5,
            cost=0.001,
            latency_ms=80,
            status="error",
            created_at=datetime(2025, 6, 1, 11, 0, 0, tzinfo=timezone.utc),
        )
    )
    db.add_all(rows)
    db.commit()
    summary = billing.summary(tenant_id=billing_tenant, month="2025-06")
    total_cost = summary.get("total_cost_usd")
    total_calls = sum(m["prompt_tokens"] + m["completion_tokens"] for m in summary.get("by_model", []))
    exp_cost = 0.01 * 4 + 0.001
    exp_tokens = (100 + 50) * 4 + (10 + 5)
    ok_g1 = total_tokens_match = abs(float(total_cost) - exp_cost) < 1e-9 and total_calls == exp_tokens
    check("G1 账单汇总手算核对（cost=0.041 / tokens=615）", ok_g1, f"tokens={total_calls} cost={total_cost}")

    # G2 告警边界（0.80 命中 / 0.79 不命中）
    alerts = AlertEngine(db)
    r_79 = alerts.check_budget_80(tenant_id=777, used_ratio=0.79, used=79.0, budget=100.0)
    r_80 = alerts.check_budget_80(tenant_id=777, used_ratio=0.80, used=80.0, budget=100.0)
    ok_g2 = (not r_79) and bool(r_80)
    check("G2 告警边界 0.79 不触发 / 0.80 触发", ok_g2, f"0.79→{bool(r_79)} 0.80→{bool(r_80)}")

    # G3 配置回滚
    from llm_platform.config_center.store import ConfigStore

    store = ConfigStore(db)
    ns = "alert"  # VALID_NAMESPACES = {routing, limit, canary, alert}
    store.put(ns, {"v": 1}, created_by="qa")
    store.put(ns, {"v": 2}, created_by="qa")
    store.rollback(ns, created_by="qa")
    cur = store.get_current(ns)
    v = cur.get("v") if isinstance(cur, dict) else None
    check("G3 配置回滚至上一版本（v=1）", v == 1, f"after rollback v={v}")
finally:
    db.close()


# ═══════════════ 汇总 ═══════════════
print(f"\n{'='*60}\n结果: {len(PASS)} PASS / {len(FAIL)} FAIL")
for name, detail in FAIL:
    print(f"  ✗ {name}  {detail}")
