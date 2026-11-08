"""M3 QA 主动测试：工程师未覆盖的验收角度（T10~T12 + 7 项遗留核销）。

覆盖：账单手算核对/导出、告警阈值边界、灰度分流比例、配置热加载/回滚、
容灾（flush 退避/任务超时/演练脚本）、MCP HTTP 链路与错误信封、
checksum 自动校验、Anthropic 真 SSE 解析、CI/Grafana 校验、跨任务对比。
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import random
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml
from fastapi.testclient import TestClient

from llm_platform.auth.api_key import generate_api_key
from llm_platform.config_center.store import ConfigStore
from llm_platform.config_center.watcher import notify_change
from llm_platform.db.repositories.call_log_repo import CallLogRepo
from llm_platform.db.repositories.key_repo import KeyRepo
from llm_platform.db.repositories.tenant_repo import TenantRepo
from llm_platform.db.session import SessionLocal, init_db
from llm_platform.eval.datasets import DatasetManager, compute_checksum
from llm_platform.eval.report import ReportGenerator
from llm_platform.eval.runner import EvalRunner
from llm_platform.gateway.buffer import CallLogBuffer
from llm_platform.main import create_app
from llm_platform.monitoring.alerts import AlertEngine
from llm_platform.monitoring.billing import BillingService
from llm_platform.routing.router import Router
from llm_platform.scheduler.worker import EvalWorker

ROOT = Path(__file__).resolve().parents[2]


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════


@pytest.fixture(scope="function")
def client():
    init_db()
    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="function")
def admin_jwt(client):
    """创建租户/Key 并换取 admin JWT。"""
    init_db()
    db = SessionLocal()
    try:
        uid = uuid.uuid4().hex[:8]
        tenant = TenantRepo(db).create_tenant(f"qa3-tenant-{uid}", monthly_budget=10000.0)
        project = TenantRepo(db).create_project(tenant.id, f"qa3-project-{uid}")
        plaintext, key_hash, key_prefix = generate_api_key()
        KeyRepo(db).create_key(
            key_hash=key_hash,
            key_prefix=key_prefix,
            tenant_id=tenant.id,
            project_id=project.id,
            scopes=["chat", "admin"],
        )
        tenant_id = tenant.id
        project_id = project.id
    finally:
        db.close()
    resp = client.post("/admin/v1/auth/token", json={"api_key": plaintext})
    assert resp.status_code == 200
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    return {"headers": headers, "tenant_id": tenant_id, "project_id": project_id}


def _make_endpoint(id_: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=id_,
        name=f"ep-{id_}",
        provider="openai",
        protocol="openai",
        base_url="http://localhost:9999",
        model_name="gpt-4o",
        credential_env="TEST_KEY",
        priority=1,
        weight=1.0,
        cost_per_1k_prompt=0.005,
        cost_per_1k_completion=0.015,
        is_active=True,
    )


def _seed_call_logs(n_per_model: dict[str, int], tenant_id: int, project_id: int) -> None:
    """插入确定性 call_log 数据（每模型固定 cost/tokens）。"""
    records = []
    for model, count in n_per_model.items():
        for i in range(count):
            records.append({
                "tenant_id": tenant_id,
                "project_id": project_id,
                "endpoint_id": 1,
                "trace_id": f"qa3-{model}-{i}",
                "model": model,
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "tokens_estimated": False,
                "cost": 0.002,
                "latency_ms": 100,
                "first_token_latency_ms": 20,
                "status": "ok",
                "error_code": None,
                "source": "api",
                "attempt_count": 1,
                "route_strategy": "cost_first",
                "prompt_hash": "x" * 16,
            })
    CallLogRepo(SessionLocal()).insert_many(records)


# ═══════════════════════════════════════════════════════════
# A. 账单（T11）
# ═══════════════════════════════════════════════════════════


class TestBilling:
    """账单 summary 与手算一致；CSV/JSON 导出合法。"""

    def test_summary_matches_hand_calc(self):
        """3 条 model-a + 2 条 model-b，cost 各 0.002 → 手算核对。"""
        init_db()
        db = SessionLocal()
        try:
            uid = uuid.uuid4().hex[:6]
            tenant = TenantRepo(db).create_tenant(f"bill-{uid}", 100.0)
            project = TenantRepo(db).create_project(tenant.id, f"bp-{uid}")
            _seed_call_logs({"model-a": 3, "model-b": 2}, tenant.id, project.id)

            svc = BillingService(db)
            summary = svc.summary(tenant_id=tenant.id)

            # 手算：model-a 3×0.002=0.006，model-b 2×0.002=0.004
            by_model = {m["model"]: m for m in summary["by_model"]}
            assert by_model["model-a"]["cost_usd"] == pytest.approx(0.006)
            assert by_model["model-b"]["cost_usd"] == pytest.approx(0.004)
            assert summary["total_cost_usd"] == pytest.approx(0.010)
            # tokens：每条 10+5=15 → model-a 45，model-b 30，total prompt 50/25
            assert by_model["model-a"]["prompt_tokens"] == 30
            assert by_model["model-a"]["completion_tokens"] == 15
            assert by_model["model-a"]["tokens"] == 45
            assert summary["total_prompt_tokens"] == 50
            assert summary["total_completion_tokens"] == 25
        finally:
            db.close()

    def test_export_csv_parseable(self):
        """CSV 可被 csv 模块解析，表头与数据行正确。"""
        init_db()
        db = SessionLocal()
        try:
            uid = uuid.uuid4().hex[:6]
            tenant = TenantRepo(db).create_tenant(f"csv-{uid}", 100.0)
            project = TenantRepo(db).create_project(tenant.id, f"cp-{uid}")
            _seed_call_logs({"model-x": 2}, tenant.id, project.id)
            csv_text = BillingService(db).export_csv(tenant_id=tenant.id)
            rows = list(csv.reader(io.StringIO(csv_text)))
            assert rows[0] == ["model", "cost_usd", "prompt_tokens", "completion_tokens", "total_tokens"]
            assert rows[1][0] == "model-x"
            assert float(rows[1][1]) == pytest.approx(0.004)
            assert rows[1][4] == "30"
        finally:
            db.close()

    def test_export_json_serializable(self):
        """JSON 导出可序列化且字段齐全。"""
        init_db()
        db = SessionLocal()
        try:
            uid = uuid.uuid4().hex[:6]
            tenant = TenantRepo(db).create_tenant(f"json-{uid}", 100.0)
            project = TenantRepo(db).create_project(tenant.id, f"jp-{uid}")
            _seed_call_logs({"model-y": 1}, tenant.id, project.id)
            data = BillingService(db).export_json(tenant_id=tenant.id)
            parsed = json.loads(json.dumps(data))
            for key in ("month", "total_cost_usd", "by_model", "by_project"):
                assert key in parsed
        finally:
            db.close()

    def test_billing_api_summary(self, client, admin_jwt):
        """GET /admin/v1/billing/summary 返回 200。"""
        resp = client.get(
            "/admin/v1/billing/summary",
            params={"tenant_id": admin_jwt["tenant_id"]},
            headers=admin_jwt["headers"],
        )
        assert resp.status_code == 200
        assert "total_cost_usd" in resp.json()

    def test_billing_api_export_csv(self, client, admin_jwt):
        """GET /admin/v1/billing/export?format=csv 返回 text/csv。"""
        resp = client.get(
            "/admin/v1/billing/export",
            params={"format": "csv", "tenant_id": admin_jwt["tenant_id"]},
            headers=admin_jwt["headers"],
        )
        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")
        assert "model" in resp.text


# ═══════════════════════════════════════════════════════════
# B. 告警（T11）
# ═══════════════════════════════════════════════════════════


class TestAlerts:
    """预算 80%/100%、错误率边界、resolve、alerts.yml 校验。"""

    def test_budget_80_boundary(self):
        """0.79 不触发 / 0.80 触发 BUDGET_80。"""
        init_db()
        db = SessionLocal()
        try:
            engine = AlertEngine(db)
            uid = uuid.uuid4().hex[:6]
            assert engine.check_budget_80(f"t-{uid}", used_ratio=0.79, used=79, budget=100) is None
            alert = engine.check_budget_80(f"t-{uid}", used_ratio=0.80, used=80, budget=100)
            assert alert is not None
            assert alert.rule_code == "BUDGET_80"
            assert alert.level == "orange"
        finally:
            db.close()

    def test_budget_100_percent_blocks(self):
        """预算 100% 必须熔断（BudgetGuard，QUOTA_002）。"""
        from llm_platform.errors import QuotaExceededError
        from llm_platform.ratelimit.budget import BudgetGuard

        guard = BudgetGuard(redis_client=None)
        tenant_id = random.randint(100000, 999999)
        guard.record_cost(tenant_id, 100.0)
        with pytest.raises(QuotaExceededError):
            guard.check_budget(tenant_id, monthly_budget=100.0, alert_ratio=0.8)

    def test_error_rate_boundary(self):
        """0.049 不触发 / 0.05 触发 ERROR_RATE。"""
        init_db()
        db = SessionLocal()
        try:
            engine = AlertEngine(db)
            assert engine.check_error_rate("endpoint", "e1", 0.049, threshold=0.05) is None
            alert = engine.check_error_rate("endpoint", "e1", 0.05, threshold=0.05)
            assert alert is not None
            assert alert.rule_code == "ERROR_RATE"
            assert alert.level == "red"
        finally:
            db.close()

    def test_alert_resolve_api(self, client, admin_jwt):
        """PATCH /admin/v1/alerts/{id}/resolve 生效。"""
        init_db()
        db = SessionLocal()
        try:
            engine = AlertEngine(db)
            alert = engine.fire(
                level="orange",
                rule_code="QA_TEST",
                subject_type="tenant",
                subject_id="1",
                message="qa 触发",
            )
            alert_id = alert.id
        finally:
            db.close()

        resp = client.patch(
            f"/admin/v1/alerts/{alert_id}/resolve", headers=admin_jwt["headers"]
        )
        assert resp.status_code == 200
        assert resp.json()["resolved"] is True

        # 列表确认 resolved=True
        listed = client.get(
            "/admin/v1/alerts",
            params={"resolved": True},
            headers=admin_jwt["headers"],
        )
        ids = [a["id"] for a in listed.json()["items"]]
        assert alert_id in ids

    def test_alerts_yaml_rules(self):
        """alerts.yml：5 组规则 + 80%/100% 预算 + YAML 合法。"""
        path = ROOT / "deploy" / "prometheus" / "alerts.yml"
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        groups = data["groups"]
        assert len(groups) == 5, f"应有 5 组规则，实际 {len(groups)}"
        alerts = {
            rule["alert"]
            for g in groups
            for rule in g["rules"]
        }
        for expected in (
            "HighErrorRate",
            "HighLatencyP95",
            "CircuitBreakerOpen",
            "BudgetExceeded80",
            "BudgetExceeded100",
            "CallLogDropped",
            "HighRateLimited",
        ):
            assert expected in alerts, f"缺少告警规则: {expected}"

    def test_alerts_yaml_metric_names_exist(self):
        """alerts.yml 引用的指标名必须在 metrics.py 真实存在。"""
        metrics_src = (ROOT / "src" / "llm_platform" / "monitoring" / "metrics.py").read_text(
            encoding="utf-8"
        )
        with open(ROOT / "deploy" / "prometheus" / "alerts.yml", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        import re

        for g in data["groups"]:
            for rule in g["rules"]:
                for name in re.findall(r"\b(llm_[a-z_]+)", rule["expr"]):
                    base = name.removesuffix("_bucket").removesuffix("_total")
                    assert (
                        f'"{name}"' in metrics_src
                        or f'"{base}"' in metrics_src
                        or name in metrics_src
                    ), f"告警 {rule['alert']} 引用不存在的指标: {name}"

    def test_promtool_if_available(self):
        """promtool check rules（本地无 promtool 则跳过并注明）。"""
        import shutil

        promtool = shutil.which("promtool")
        if promtool is None:
            pytest.skip("本地无 promtool，已做 YAML 语法+语义人工核对（见 test_alerts_yaml_rules）")
        result = subprocess.run(
            [promtool, "check", "rules", str(ROOT / "deploy" / "prometheus" / "alerts.yml")],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr


# ═══════════════════════════════════════════════════════════
# C. 灰度 + 配置热加载/回滚（T12 / P1-09）
# ═══════════════════════════════════════════════════════════


class TestCanaryAndConfig:
    def test_canary_ratio_1000(self):
        """set_canary(0.1) 后 1000 次选路，灰度占比在统计误差内（5%~15%）。"""
        random.seed(42)
        router = Router()
        eps = [_make_endpoint(1), _make_endpoint(2)]
        router.set_canary([2], 0.1)
        hits = sum(1 for _ in range(1000) if router.select_endpoint(eps).id == 2)
        assert 50 <= hits <= 150, f"灰度占比失真: {hits}/1000（期望约 100）"
        router.clear_canary()

    def test_canary_weight_clamped(self):
        """权重越界自动钳制到 [0,1]。"""
        router = Router()
        router.set_canary([1], 1.5)
        assert router.canary_weight == 1.0
        router.set_canary([1], -0.5)
        assert router.canary_weight == 0.0

    def test_config_pubsub_notify(self):
        """模拟 publish：notify_change 触发订阅回调。"""
        from llm_platform.config_center.watcher import register_callback

        received: list[dict] = []
        ns = f"test-ns-{uuid.uuid4().hex[:4]}"
        register_callback(ns, received.append)
        notify_change(ns, {"canary_weight": 0.2})
        assert received == [{"canary_weight": 0.2}]

    def test_poll_versions_detects_change(self):
        """poll_versions 拉到新版本 → 触发 notify（轮询兜底）。"""
        import llm_platform.config_center.watcher as watcher_mod

        init_db()
        db = SessionLocal()
        try:
            store = ConfigStore(db)
            ns = "routing"
            received: list[dict] = []
            watcher_mod._callbacks.setdefault(ns, []).append(received.append)
            old = watcher_mod._last_versions.get(ns, 0)

            store.put(ns, {"strategy": "cost_first", "v": 1}, created_by="qa")
            watcher_mod.ConfigWatcher().poll_versions(store)
            first_count = len(received)
            assert first_count >= 1, "poll_versions 未检测到新版本"
            assert received[-1].get("v") == 1

            # 无新版本时不再触发
            watcher_mod.ConfigWatcher().poll_versions(store)
            assert len(received) == first_count

            # 再次提交 → 再次触发
            store.put(ns, {"strategy": "latency_first", "v": 2}, created_by="qa")
            watcher_mod.ConfigWatcher().poll_versions(store)
            assert len(received) == first_count + 1
            assert received[-1].get("v") == 2
            watcher_mod._last_versions[ns] = old
        finally:
            db.close()

    def test_config_rollback_restores_previous(self):
        """回滚到旧 revision 后配置值还原。"""
        init_db()
        db = SessionLocal()
        try:
            store = ConfigStore(db)
            ns = "canary"
            store.put(ns, {"canary_weight": 0.1}, created_by="qa")
            store.put(ns, {"canary_weight": 0.5}, created_by="qa")
            assert store.get_current(ns)["canary_weight"] == 0.5

            rev = store.rollback(ns, created_by="qa")
            current = store.get_current(ns)
            assert current["canary_weight"] == 0.1, f"回滚后应还原 0.1，实际 {current}"
            assert rev.version >= 3  # 回滚=以旧 payload 新版本
        finally:
            db.close()

    def test_config_api_put_get_rollback(self, client, admin_jwt):
        """配置 API：PUT → GET → rollback → GET。"""
        h = admin_jwt["headers"]
        ns = "limit"
        r1 = client.put(f"/admin/v1/config/{ns}", json={"payload": {"rpm": 100}}, headers=h)
        assert r1.status_code == 200
        assert r1.json()["version"] >= 1

        client.put(f"/admin/v1/config/{ns}", json={"payload": {"rpm": 200}}, headers=h)
        got = client.get(f"/admin/v1/config/{ns}", headers=h)
        assert got.json()["payload"]["rpm"] == 200

        rb = client.post(f"/admin/v1/config/{ns}/rollback", headers=h)
        assert rb.status_code == 200
        got2 = client.get(f"/admin/v1/config/{ns}", headers=h)
        assert got2.json()["payload"]["rpm"] == 100, "回滚后配置应还原"

    def test_config_invalid_namespace_rejected(self):
        """非法命名空间 → ConfigError。"""
        from llm_platform.errors import ConfigError

        init_db()
        db = SessionLocal()
        try:
            store = ConfigStore(db)
            with pytest.raises(ConfigError):
                store.put("bad-ns", {}, created_by="qa")
        finally:
            db.close()


# ═══════════════════════════════════════════════════════════
# D. 容灾（T12 / P1-11）
# ═══════════════════════════════════════════════════════════


class TestResilience:
    def test_flush_retry_and_drop_count(self):
        """flush 持续失败：3 次退避重试 → 进重试缓冲；缓冲满 → 丢弃计数。"""
        calls: list[int] = []

        def failing_flush(batch):  # noqa: ANN001
            calls.append(len(batch))
            raise RuntimeError("db down")

        buf = CallLogBuffer(retry_base_delay_s=0.01, max_retries=3)
        buf.set_flush_fn(failing_flush)

        async def run():
            batch = [{"i": i} for i in range(3)]
            await buf._flush_with_retry(batch)
            assert len(calls) == 3, f"应退避重试 3 次，实际 {len(calls)}"
            assert len(buf._retry_buffer) == 3, "重试耗尽应进入重试缓冲"

            # 缓冲塞满（>10000）后再失败 → 丢弃
            buf._retry_buffer = [{"x": 1}] * 10_000
            with patch(
                "llm_platform.gateway.buffer.record_calllog_dropped"
            ) as dropped:
                await buf._flush_with_retry([{"y": 1}])
                assert dropped.called, "缓冲满后应触发丢弃计数"

        asyncio.run(run())

    def test_task_timeout_triggers(self):
        """任务级超时：run_task 超时 → 任务标记 failed + 超时错误。"""
        init_db()
        db = SessionLocal()
        try:
            uid = uuid.uuid4().hex[:6]
            from llm_platform.db.repositories.eval_repo import EvalRepo

            repo = EvalRepo(db)
            task = repo.create_task(
                name=f"timeout-{uid}",
                dataset_id=1,
                models=["m1"],
                config={},
                status="pending",
                progress=0.0,
                created_by="qa",
            )
            task_id = task.id
        finally:
            db.close()

        async def slow_run_task(*args, **kwargs):  # noqa: ANN001
            await asyncio.sleep(5)
            return []

        mock_runner = MagicMock()
        mock_runner.run_task = slow_run_task

        async def run():
            worker = EvalWorker(task_timeout_s=0.2)
            with patch(
                "llm_platform.scheduler.worker.EvalRunner", return_value=mock_runner
            ):
                ok = await worker.process_task(task_id, {"dataset_id": 1, "models": ["m1"]})
            assert ok is False

        asyncio.run(run())

        db = SessionLocal()
        try:
            from llm_platform.db.repositories.eval_repo import EvalRepo

            stored = EvalRepo(db).get_task(task_id)
            assert stored.status == "failed"
            assert "超时" in (stored.error or "")
        finally:
            db.close()

    def test_drill_scripts_syntax(self):
        """failover_drill.sh / canary_drill.sh bash 语法正确。"""
        for name in ("failover_drill.sh", "canary_drill.sh"):
            path = ROOT / "scripts" / name
            assert path.exists(), f"缺少脚本: {name}"
            result = subprocess.run(
                ["bash", "-n", str(path)], capture_output=True, text=True
            )
            assert result.returncode == 0, f"{name} 语法错误: {result.stderr}"


# ═══════════════════════════════════════════════════════════
# E. MCP HTTP API（遗留②）
# ═══════════════════════════════════════════════════════════


class TestMcpApi:
    """注册→列表→调用链路；未知工具统一错误信封。"""

    def test_mcp_register_list_call_chain(self, client, admin_jwt):
        """POST /mcp/tools 注册 → GET 列表 → POST /mcp/call 调用。"""
        h = admin_jwt["headers"]
        reg = client.post(
            "/admin/v1/mcp/tools",
            json={
                "name": f"qa-echo-{uuid.uuid4().hex[:6]}",
                "handler": "echo",
                "input_schema": {"type": "object"},
                "description": "qa 测试工具",
            },
            headers=h,
        )
        assert reg.status_code == 200, f"注册应 200，实际 {reg.status_code}: {reg.text[:200]}"

        listed = client.get("/admin/v1/mcp/tools", headers=h)
        assert listed.status_code == 200, f"列表应 200，实际 {listed.status_code}"
        names = [t["name"] for t in listed.json()]
        assert reg.json()["name"] in names

        called = client.post(
            "/admin/v1/mcp/call",
            json={"tool_name": reg.json()["name"], "arguments": {"message": "hi"}},
            headers=h,
        )
        assert called.status_code == 200
        body = called.json()
        assert body["success"] is True
        assert body["latency_ms"] >= 0

    def test_mcp_unknown_tool_unified_envelope(self, client, admin_jwt):
        """未知工具 → 统一错误信封（NOT_FOUND_006），非裸 500。"""
        h = admin_jwt["headers"]
        resp = client.post(
            "/admin/v1/mcp/call",
            json={"tool_name": "no-such-tool", "arguments": {}},
            headers=h,
        )
        assert resp.status_code == 404, f"未知工具应 404，实际 {resp.status_code}: {resp.text[:200]}"
        err = resp.json().get("error", {})
        assert err.get("code") == "NOT_FOUND_006"
        assert "message" in err


# ═══════════════════════════════════════════════════════════
# F. checksum 自动校验（遗留③）
# ═══════════════════════════════════════════════════════════


class TestChecksumAutoVerify:
    def test_tampered_dataset_rejected_on_run(self):
        """篡改基准集后执行评分必须被拒（fail-closed）。"""
        init_db()
        db = SessionLocal()
        try:
            mgr = DatasetManager(db)
            uid = uuid.uuid4().hex[:6]
            items = [
                {"input_prompt": "q1", "expected_output": "a1", "metadata": {"d": "x"}},
                {"input_prompt": "q2", "expected_output": "a2", "metadata": {"d": "y"}},
            ]
            result = mgr.register(f"tamper-run-{uid}", f"1.0.{uid}", items)

            # 篡改 DB 中的样本
            from llm_platform.models.entities import EvalDatasetItem

            rows = (
                db.query(EvalDatasetItem)
                .filter(EvalDatasetItem.dataset_id == result["id"])
                .all()
            )
            rows[0].expected_output = "HACKED"
            db.commit()

            runner = EvalRunner(db, redis_client=None)
            from llm_platform.errors import ConflictError, ValidationError

            with pytest.raises((ValidationError, ConflictError)):
                asyncio.run(
                    runner.run_task(
                        task_id=999,
                        dataset_id=result["id"],
                        models=["qa-nonexistent-model"],
                        config={},
                    )
                )
        finally:
            db.close()

    def test_checksum_autoverify_consistent(self):
        """注册 checksum 与 get_items 重算 checksum 必须一致（防误报）。"""
        init_db()
        db = SessionLocal()
        try:
            mgr = DatasetManager(db)
            uid = uuid.uuid4().hex[:6]
            # 两种常见形状：无 metadata 键 / 带 metadata 键
            shapes = [
                [{"input_prompt": "a", "expected_output": "b"}],
                [{"input_prompt": "a", "expected_output": "b", "metadata": {}}],
            ]
            for i, items in enumerate(shapes):
                r = mgr.register(f"shape-{i}-{uid}", f"1.0.{uid}-{i}", items)
                recomputed = compute_checksum(mgr.get_items(r["id"]))
                assert recomputed == r["checksum"], (
                    f"形状 {i} checksum 规范化不一致：注册 {r['checksum'][:12]} "
                    f"vs 重算 {recomputed[:12]}（会导致自动校验误报篡改）"
                )
        finally:
            db.close()


# ═══════════════════════════════════════════════════════════
# G. Anthropic 真 SSE（遗留⑥）
# ═══════════════════════════════════════════════════════════


class TestAnthropicSse:
    def test_event_stream_parsing(self):
        """构造 Anthropic event 流：逐 chunk 解析文本与 usage。"""
        from llm_platform.adapters.anthropic_adapter import AnthropicAdapter
        from llm_platform.adapters.base import ChatMessage, ChatRequest

        sse_lines = [
            'event: message_start',
            'data: {"message": {"usage": {"input_tokens": 12}}}',
            "event: content_block_delta",
            'data: {"delta": {"type": "text_delta", "text": "你"}}',
            "event: content_block_delta",
            'data: {"delta": {"type": "text_delta", "text": "好"}}',
            "event: message_delta",
            'data: {"delta": {"stop_reason": "stop"}, "usage": {"output_tokens": 5}}',
            "event: message_stop",
            "data: {}",
        ]

        class FakeResp:
            def raise_for_status(self):
                return None

            async def aiter_lines(self):
                for line in sse_lines:
                    yield line

        class FakeStreamCtx:
            async def __aenter__(self):
                return FakeResp()

            async def __aexit__(self, *args):
                return False

        fake_client = MagicMock()
        fake_client.stream = MagicMock(return_value=FakeStreamCtx())

        async def fake_get_client():
            return fake_client

        adapter = AnthropicAdapter()
        adapter._get_client = fake_get_client  # type: ignore[method-assign]

        async def run():
            request = ChatRequest(
                model="claude-3-5-sonnet",
                messages=[ChatMessage(role="user", content="hi")],
                stream=True,
            )
            chunks = []
            async for chunk in adapter.chat_stream(
                request, base_url="http://mock", api_key="k"
            ):
                chunks.append(chunk)
            return chunks

        chunks = asyncio.run(run())
        # 逐段解析：role delta + 2 个文本 delta + 1 个收尾 usage chunk
        contents = [
            c.choices[0]["delta"].get("content")
            for c in chunks
            if c.choices and c.choices[0]["delta"].get("content")
        ]
        assert contents == ["你", "好"], f"SSE 文本应逐段解析，实际 {contents}"
        usage_chunks = [c for c in chunks if c.usage]
        assert usage_chunks, "应有 usage chunk"
        assert usage_chunks[-1].usage.prompt_tokens == 12
        assert usage_chunks[-1].usage.completion_tokens == 5
        # 不再整段切分：文本必须来自独立 event 而非一次性输出
        assert len(contents) == 2

    def test_event_stream_error_raises_upstream(self):
        """error event → UpstreamError。"""
        from llm_platform.adapters.anthropic_adapter import AnthropicAdapter
        from llm_platform.adapters.base import ChatMessage, ChatRequest
        from llm_platform.errors import UpstreamError

        sse_lines = [
            "event: error",
            'data: {"error": {"message": "overloaded"}}',
        ]

        class FakeResp:
            def raise_for_status(self):
                return None

            async def aiter_lines(self):
                for line in sse_lines:
                    yield line

        class FakeStreamCtx:
            async def __aenter__(self):
                return FakeResp()

            async def __aexit__(self, *args):
                return False

        fake_client = MagicMock()
        fake_client.stream = MagicMock(return_value=FakeStreamCtx())

        async def fake_get_client():
            return fake_client

        adapter = AnthropicAdapter()
        adapter._get_client = fake_get_client  # type: ignore[method-assign]

        async def run():
            request = ChatRequest(
                model="claude-3-5-sonnet",
                messages=[ChatMessage(role="user", content="hi")],
                stream=True,
            )
            async for _ in adapter.chat_stream(request, base_url="http://mock", api_key="k"):
                pass

        with pytest.raises(UpstreamError):
            asyncio.run(run())


# ═══════════════════════════════════════════════════════════
# H. CI / Grafana（T10/T11）
# ═══════════════════════════════════════════════════════════


class TestCiAndGrafana:
    def test_ci_yml_structure(self):
        """ci.yml：lint→test 依赖、pip 缓存、junitxml artifact、gitleaks、Docker job。"""
        with open(ROOT / ".github" / "workflows" / "ci.yml", encoding="utf-8") as f:
            ci = yaml.safe_load(f)
        jobs = ci["jobs"]
        for job in ("lint", "test", "gitleaks", "build-docker"):
            assert job in jobs, f"缺少 job: {job}"
        assert jobs["test"].get("needs") == "lint", "test 应依赖 lint"
        # pip 缓存
        setup_steps = [
            s for s in jobs["test"]["steps"] if str(s.get("uses", "")).startswith("actions/setup-python")
        ]
        assert any(s.get("with", {}).get("cache") == "pip" for s in setup_steps), "缺少 pip 缓存"
        # junitxml + artifact
        run_cmds = " ".join(str(s.get("run", "")) for s in jobs["test"]["steps"])
        assert "--junitxml" in run_cmds, "缺少 junitxml 测试报告"
        assert any(
            str(s.get("uses", "")).startswith("actions/upload-artifact")
            for s in jobs["test"]["steps"]
        ), "缺少测试报告 artifact"
        # gitleaks 为独立门槛 job
        gitleaks_uses = " ".join(str(s.get("uses", "")) for s in jobs["gitleaks"]["steps"])
        assert "gitleaks" in gitleaks_uses, "gitleaks 步骤缺失"
        # Docker build job
        assert "docker build" in " ".join(
            str(s.get("run", "")) for s in jobs["build-docker"]["steps"]
        )

    def test_grafana_dashboards_valid(self):
        """3 个看板 JSON 合法 + 查询引用的指标在 metrics.py 存在。"""
        metrics_src = (ROOT / "src" / "llm_platform" / "monitoring" / "metrics.py").read_text(
            encoding="utf-8"
        )
        dash_dir = ROOT / "deploy" / "grafana" / "dashboards"
        files = sorted(dash_dir.glob("*.json"))
        assert len(files) == 3, f"应有 3 个看板，实际 {len(files)}"
        import re

        for f in files:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)  # 必须合法
            # 兼容 Grafana 导出包装 {"dashboard": {...}} 与扁平结构
            dashboard = data.get("dashboard", data)
            panels = dashboard.get("panels", [])
            assert panels, f"{f.name} 无 panels"
            exprs: list[str] = []
            for panel in panels:
                for target in panel.get("targets", []):
                    if "expr" in target:
                        exprs.append(target["expr"])
                for inner in panel.get("panels", []):  # 行面板嵌套
                    for target in inner.get("targets", []):
                        if "expr" in target:
                            exprs.append(target["expr"])
            for expr in exprs:
                for name in re.findall(r"\b(llm_[a-z_]+)", expr):
                    base = name.removesuffix("_bucket").removesuffix("_total")
                    assert (
                        f'"{name}"' in metrics_src
                        or f'"{base}"' in metrics_src
                        or name in metrics_src
                    ), f"{f.name} 引用不存在的指标: {name}"


# ═══════════════════════════════════════════════════════════
# I. 跨任务对比（遗留④）
# ═══════════════════════════════════════════════════════════


class TestCrossTaskCompare:
    def test_compare_cross_task_trends(self):
        """跨任务对比：model_trends 聚合 + best_task 判定。"""
        gen = ReportGenerator()
        reports = [
            {"task_id": 1, "model": "m-a", "overall_score": 0.8, "metrics": {"accuracy": 0.8}},
            {"task_id": 1, "model": "m-b", "overall_score": 0.6, "metrics": {"accuracy": 0.6}},
            {"task_id": 2, "model": "m-a", "overall_score": 0.9, "metrics": {"accuracy": 0.9}},
            {"task_id": 2, "model": "m-b", "overall_score": 0.7, "metrics": {"accuracy": 0.7}},
        ]
        result = gen.compare_cross_task(reports)
        assert set(result["model_trends"].keys()) == {"m-a", "m-b"}
        assert len(result["model_trends"]["m-a"]) == 2
        assert result["best_task"]["task_id"] == 2
        assert result["best_task"]["model"] == "m-a"
        assert result["best_task"]["overall_score"] == 0.9
        assert len(result["tasks"]) == 4

    def test_compare_cross_task_empty(self):
        gen = ReportGenerator()
        result = gen.compare_cross_task([])
        assert result == {"tasks": [], "model_trends": {}, "best_task": {}}
