"""M2 QA 主动测试：工程师未覆盖的验收角度（T07~T09 + M1 遗留回归）。

覆盖：指标手算核对、overall_score 越界、基准集防篡改、报告溯源、
调度状态机（非法迁移/重试/取消）、eval:rl: 限流隔离、MCP 审计落库、
SRC-1/SRC-2 回归、评估端到端、API 错误信封、P1-02 固定基准集内置种子。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from llm_platform.adapters.base import StreamChunk, Usage
from llm_platform.auth.api_key import generate_api_key
from llm_platform.auth.jwt import create_access_token
from llm_platform.config import get_settings
from llm_platform.db.repositories.call_log_repo import CallLogRepo
from llm_platform.db.repositories.endpoint_repo import EndpointRepo
from llm_platform.db.repositories.eval_repo import EvalRepo
from llm_platform.db.repositories.key_repo import KeyRepo
from llm_platform.db.repositories.tenant_repo import TenantRepo
from llm_platform.db.session import SessionLocal, init_db
from llm_platform.errors import AuthError, ConflictError, RateLimitError, ValidationError
from llm_platform.eval.datasets import DatasetManager
from llm_platform.eval.mcp_gateway import McpGateway
from llm_platform.eval.metrics import compute_metrics, overall_score
from llm_platform.eval.report import ReportGenerator
from llm_platform.eval.runner import EvalRunner
from llm_platform.gateway.buffer import CallLogBuffer
from llm_platform.main import create_app
from llm_platform.ratelimit.token_bucket import TokenBucket
from llm_platform.scheduler.queue import EvalQueue
from llm_platform.scheduler.state_machine import TaskStateMachine
from llm_platform.scheduler.worker import EvalWorker

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
def qa2_setup_data():
    """初始化测试数据：租户/项目/Key（admin scope）/端点。"""
    init_db()
    db = SessionLocal()
    try:
        uid = uuid.uuid4().hex[:8]
        tenant_repo = TenantRepo(db)
        tenant = tenant_repo.create_tenant(f"qa2-tenant-{uid}", monthly_budget=10000.0)
        project = tenant_repo.create_project(tenant.id, f"qa2-project-{uid}")

        plaintext, key_hash, key_prefix = generate_api_key()
        key_repo = KeyRepo(db)
        key = key_repo.create_key(
            key_hash=key_hash,
            key_prefix=key_prefix,
            tenant_id=tenant.id,
            project_id=project.id,
            scopes=["chat", "admin"],
        )

        endpoint_repo = EndpointRepo(db)
        model_name = f"qa2-model-{uid}"
        endpoint = endpoint_repo.create_endpoint(
            provider="openai",
            protocol="openai",
            model_name=model_name,
            base_url="http://mock-upstream/v1",
            credential_env="LLM_OPENAI_API_KEY",
            weight=1.0,
            priority=10,
            cost_per_1k_prompt=0.001,
            cost_per_1k_completion=0.002,
        )

        yield {
            "tenant": tenant,
            "project": project,
            "key_plaintext": plaintext,
            "key_id": key.id,
            "endpoint": endpoint,
            "model_name": model_name,
        }
    finally:
        db.close()


@pytest.fixture(scope="function")
def eval_jwt(client, qa2_setup_data):
    """换取 evaluator/admin JWT。"""
    resp = client.post(
        "/admin/v1/auth/token",
        json={"api_key": qa2_setup_data["key_plaintext"]},
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# ═══════════════════════════════════════════════════════════
# A. 指标正确性（手算核对）
# ═══════════════════════════════════════════════════════════


class TestMetricCorrectness:
    """手工构造 y_true/y_pred，核对指标数值与手算一致。"""

    def test_accuracy_hand_computed(self):
        """2/4 精确匹配 → accuracy = 0.5。"""
        from llm_platform.eval.metrics import accuracy

        preds = ["a", "b", "x", "y"]
        refs = ["a", "b", "c", "d"]
        assert accuracy(preds, refs) == pytest.approx(0.5)

    def test_f1_hand_computed(self):
        """token 集合 {hello,world} vs {hello,there}：
        common={hello}, P=0.5, R=0.5, F1=0.5。"""
        from llm_platform.eval.metrics import f1_score

        val = f1_score(["hello world"], ["hello there"])
        assert val == pytest.approx(0.5)

    def test_f1_partial_hand_computed(self):
        """{the,cat,sat} vs {the,cat,mat}：common={the,cat}。
        P=2/3, R=2/3, F1=2/3。"""
        from llm_platform.eval.metrics import f1_score

        val = f1_score(["the cat sat"], ["the cat mat"])
        assert val == pytest.approx(2 / 3)

    def test_rouge_l_hand_computed(self):
        """LCS([a,b,c],[a,c,b]) = 2；P=R=2/3 → F1=2/3。"""
        from llm_platform.eval.metrics import rouge_l

        val = rouge_l(["a b c"], ["a c b"])
        assert val == pytest.approx(2 / 3)

    def test_rouge_l_lcs_weighted_hand_computed(self):
        """pred=[the,cat,sat] vs ref=[the,cat,sat,on,the,mat]：
        LCS=3, P=3/3=1, R=3/6=0.5, F1=2/3。"""
        from llm_platform.eval.metrics import rouge_l

        val = rouge_l(["the cat sat"], ["the cat sat on the mat"])
        assert val == pytest.approx(2 / 3)

    def test_overall_score_hand_computed(self):
        """加权手算：0.8*0.35 + 0.8*0.25 + 0.8*0.25 + 0.0*0.15 = 0.68。"""
        metrics = {
            "accuracy": 0.8,
            "f1": 0.8,
            "rouge_l": 0.8,
            "tool_success_rate": 0.0,
        }
        assert overall_score(metrics) == pytest.approx(0.68, abs=1e-6)

    def test_overall_score_perfect_and_zero(self):
        perfect = {
            "accuracy": 1.0,
            "f1": 1.0,
            "rouge_l": 1.0,
            "tool_success_rate": 1.0,
        }
        zeros = {
            "accuracy": 0.0,
            "f1": 0.0,
            "rouge_l": 0.0,
            "tool_success_rate": 0.0,
        }
        assert overall_score(perfect) == pytest.approx(1.0)
        assert overall_score(zeros) == pytest.approx(0.0)

    def test_overall_score_within_bounds(self):
        """归一化指标任意合法取值（0~1），加权总分不得越界。"""
        values = [0.0, 0.13, 0.25, 0.5, 0.77, 0.99, 1.0]
        for a in values:
            for f in values:
                metrics = {
                    "accuracy": a,
                    "f1": f,
                    "rouge_l": (a + f) / 2,
                    "tool_success_rate": min(1.0, a + 0.1),
                }
                score = overall_score(metrics)
                assert 0.0 <= score <= 1.0, f"越界: {score} for {metrics}"

    def test_overall_score_ignores_observational_metrics(self):
        """延迟/Token/成本为观测维度，不参与加权。"""
        base = {"accuracy": 0.6, "f1": 0.6, "rouge_l": 0.6, "tool_success_rate": 0.6}
        with_obs = {
            **base,
            "latency_p95_ms": 99999.0,
            "tokens": 10_000_000,
            "cost_usd": 12345.6,
        }
        assert overall_score(base) == overall_score(with_obs)

    def test_compute_metrics_subset_names(self):
        """metric_names 只计算指定指标。"""
        result = compute_metrics(
            ["a"], ["a"], metric_names=["accuracy", "tokens"]
        )
        assert set(result.keys()) == {"accuracy", "tokens"}
        assert result["accuracy"] == 1.0


# ═══════════════════════════════════════════════════════════
# B. 基准集防篡改 + 版本化
# ═══════════════════════════════════════════════════════════


class TestDatasetAntiTamper:
    """checksum 防篡改、版本化字段齐全、非法输入拒绝。"""

    def test_checksum_detects_tampering(self):
        """注册后改数据，checksum 校验必须不通过（防篡改）。"""
        init_db()
        db = SessionLocal()
        try:
            mgr = DatasetManager(db)
            uid = uuid.uuid4().hex[:8]
            items = [
                {"input_prompt": "q1", "expected_output": "a1"},
                {"input_prompt": "q2", "expected_output": "a2"},
            ]
            result = mgr.register(f"tamper-{uid}", f"1.0.{uid[:4]}", items)

            # 原始数据校验通过
            assert mgr.verify_checksum(result["id"], items) is True

            # 篡改任一字段后校验必须失败
            tampered = [
                {"input_prompt": "q1", "expected_output": "a1-HACKED"},
                {"input_prompt": "q2", "expected_output": "a2"},
            ]
            assert mgr.verify_checksum(result["id"], tampered) is False

            # 删除样本后校验也必须失败
            assert mgr.verify_checksum(result["id"], items[:1]) is False
        finally:
            db.close()

    def test_version_fields_complete(self):
        """版本化字段齐全：id/name/version/item_count/checksum/source。"""
        init_db()
        db = SessionLocal()
        try:
            mgr = DatasetManager(db)
            uid = uuid.uuid4().hex[:8]
            items = [{"input_prompt": "p", "expected_output": "r"}]
            result = mgr.register(
                f"fields-{uid}", f"1.0.{uid[:4]}", items, source="qa-upload"
            )
            for field in ("id", "name", "version", "item_count", "checksum"):
                assert field in result, f"缺少字段: {field}"
            assert len(result["checksum"]) == 64  # SHA-256

            listed = [d for d in mgr.list_datasets() if d["id"] == result["id"]][0]
            assert listed["source"] == "qa-upload"
            assert listed["version"] == result["version"]
        finally:
            db.close()

    def test_duplicate_version_rejected(self):
        """同版本重复注册 → ConflictError（CONFLICT_009）。"""
        init_db()
        db = SessionLocal()
        try:
            mgr = DatasetManager(db)
            uid = uuid.uuid4().hex[:8]
            items = [{"input_prompt": "p", "expected_output": "r"}]
            version = f"1.0.{uid[:4]}"
            mgr.register(f"dup-{uid}", version, items)
            with pytest.raises(ConflictError) as exc:
                mgr.register(f"dup2-{uid}", version, items)
            assert exc.value.code == "CONFLICT_009"
        finally:
            db.close()

    def test_empty_items_rejected(self):
        """空样本 → ValidationError（VALID_005）。"""
        init_db()
        db = SessionLocal()
        try:
            mgr = DatasetManager(db)
            with pytest.raises(ValidationError) as exc:
                mgr.register("empty-ds", "9.9.9", [])
            assert exc.value.code == "VALID_005"
        finally:
            db.close()

    def test_missing_input_prompt_rejected(self):
        """样本缺 input_prompt → ValidationError。"""
        init_db()
        db = SessionLocal()
        try:
            mgr = DatasetManager(db)
            with pytest.raises(ValidationError):
                mgr.register("bad-ds", "9.9.8", [{"expected_output": "x"}])
        finally:
            db.close()


# ═══════════════════════════════════════════════════════════
# C. 报告溯源 + API 错误信封
# ═══════════════════════════════════════════════════════════


class TestReportTraceability:
    """报告含 dataset_version/evaluator_version 溯源；缺基准集走统一信封。"""

    def test_report_contains_traceability_fields(self):
        """报告 JSON 含 dataset_version + evaluator_version（data-model §2.8）。"""
        gen = ReportGenerator()
        report = gen.generate(1, "m", {"accuracy": 1.0}, 0.9, "1.2.0")
        assert report["dataset_version"] == "1.2.0"
        assert report["evaluator_version"] == "v1"
        # 可溯源字段可序列化
        parsed = json.loads(gen.export_json(report))
        assert parsed["dataset_version"] == "1.2.0"
        assert parsed["evaluator_version"] == "v1"

    def test_report_persisted_with_trace_fields(self):
        """eval_report 落库含 dataset_version/evaluator_version。"""
        init_db()
        db = SessionLocal()
        try:
            uid = uuid.uuid4().hex[:8]
            repo = EvalRepo(db)
            task = repo.create_task(
                name=f"trace-task-{uid}",
                dataset_id=1,
                models=["m1"],
                config={},
                status="pending",
                progress=0.0,
                created_by="qa",
            )
            repo.create_report(
                task_id=task.id,
                model="m1",
                overall_score=0.5,
                metrics={"accuracy": 0.5},
                dataset_version="2.0.0",
                evaluator_version="v1",
            )
            reports = repo.list_reports(task.id)
            assert len(reports) == 1
            assert reports[0].dataset_version == "2.0.0"
            assert reports[0].evaluator_version == "v1"
        finally:
            db.close()

    def test_eval_task_missing_dataset_not_found_envelope(
        self, client, eval_jwt
    ):
        """缺基准集启动评测 → 404 NOT_FOUND_006 统一错误信封。"""
        resp = client.post(
            "/admin/v1/eval-tasks",
            json={
                "name": "missing-ds-task",
                "dataset_id": 999999,
                "models": ["m1"],
                "config": {},
            },
            headers=eval_jwt,
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body["error"]["code"] == "NOT_FOUND_006"
        assert "message" in body["error"]

    def test_dataset_duplicate_version_conflict_envelope(
        self, client, eval_jwt
    ):
        """重复版本注册 → 409 CONFLICT_009 统一错误信封。"""
        uid = uuid.uuid4().hex[:8]
        version = f"3.0.{uid[:4]}"
        payload = {
            "name": f"conflict-ds-{uid}",
            "version": version,
            "items": [{"input_prompt": "q", "expected_output": "a"}],
        }
        first = client.post(
            "/admin/v1/eval-datasets", json=payload, headers=eval_jwt
        )
        assert first.status_code == 201

        second = client.post(
            "/admin/v1/eval-datasets", json=payload, headers=eval_jwt
        )
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "CONFLICT_009"

    def test_reports_missing_task_not_found(self, client, eval_jwt):
        """查询不存在任务的报告 → 404 NOT_FOUND_006。"""
        resp = client.get(
            "/admin/v1/eval-tasks/999999/reports", headers=eval_jwt
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "NOT_FOUND_006"

    def test_export_missing_report_not_found(self, client, eval_jwt):
        """导出不存在的报告 → 404 NOT_FOUND_006。"""
        resp = client.get(
            "/admin/v1/eval-reports/999999/export", headers=eval_jwt
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "NOT_FOUND_006"


# ═══════════════════════════════════════════════════════════
# D. 调度状态机
# ═══════════════════════════════════════════════════════════


class TestSchedulerStateMachine:
    """非法迁移拒绝、failed→pending 重试、cancel 生效。"""

    def test_illegal_success_to_running_rejected(self):
        """success→running 非法迁移必须被拒（CONFLICT_009）。"""
        assert TaskStateMachine.validate_transition("success", "running") is False
        with pytest.raises(ConflictError) as exc:
            TaskStateMachine.transition("success", "running")
        assert exc.value.code == "CONFLICT_009"

    def test_illegal_cancelled_to_running_rejected(self):
        assert TaskStateMachine.validate_transition("cancelled", "running") is False
        with pytest.raises(ConflictError):
            TaskStateMachine.transition("cancelled", "running")

    def test_illegal_pending_to_success_skipping_run(self):
        """pending 不得直接跳 success。"""
        assert TaskStateMachine.validate_transition("pending", "success") is False

    def test_failed_to_pending_retry_allowed(self):
        """failed→pending 允许重试。"""
        assert TaskStateMachine.validate_transition("failed", "pending") is True
        assert TaskStateMachine.transition("failed", "pending") == "pending"

    def test_cancel_pending_task_via_api(self, client, eval_jwt):
        """API 取消 pending 任务生效（响应 + DB 双重确认）。"""
        # 先注册基准集
        uid = uuid.uuid4().hex[:8]
        ds = client.post(
            "/admin/v1/eval-datasets",
            json={
                "name": f"cancel-ds-{uid}",
                "version": f"1.0.{uid[:4]}",
                "items": [{"input_prompt": "q", "expected_output": "a"}],
            },
            headers=eval_jwt,
        )
        assert ds.status_code == 201
        dataset_id = ds.json()["id"]

        task = client.post(
            "/admin/v1/eval-tasks",
            json={
                "name": f"cancel-task-{uid}",
                "dataset_id": dataset_id,
                "models": ["m1"],
                "config": {},
            },
            headers=eval_jwt,
        )
        assert task.status_code == 202
        task_id = task.json()["id"]
        assert task.json()["status"] == "pending"

        cancel = client.post(
            f"/admin/v1/eval-tasks/{task_id}/cancel", headers=eval_jwt
        )
        assert cancel.status_code == 200
        assert cancel.json()["status"] == "cancelled"

        # DB 状态确认（cancel 生效）
        db = SessionLocal()
        try:
            stored = EvalRepo(db).get_task(task_id)
            assert stored.status == "cancelled"
        finally:
            db.close()

    def test_get_eval_task_returns_status(self, client, eval_jwt):
        """GET /admin/v1/eval-tasks/{id} 返回任务状态/进度（api-contract §2.7）。"""
        uid = uuid.uuid4().hex[:8]
        ds = client.post(
            "/admin/v1/eval-datasets",
            json={
                "name": f"status-ds-{uid}",
                "version": f"1.0.{uid[:4]}",
                "items": [{"input_prompt": "q", "expected_output": "a"}],
            },
            headers=eval_jwt,
        )
        assert ds.status_code == 201
        dataset_id = ds.json()["id"]

        task = client.post(
            "/admin/v1/eval-tasks",
            json={
                "name": f"status-task-{uid}",
                "dataset_id": dataset_id,
                "models": ["m1"],
                "config": {},
            },
            headers=eval_jwt,
        )
        assert task.status_code == 202
        task_id = task.json()["id"]

        detail = client.get(
            f"/admin/v1/eval-tasks/{task_id}", headers=eval_jwt
        )
        assert detail.status_code == 200
        body = detail.json()
        assert body["id"] == task_id
        assert body["status"] == "pending"
        assert "progress" in body

    def test_cancel_terminal_success_rejected(self):
        """success 已是终态，不得再取消。"""
        with pytest.raises(ConflictError):
            TaskStateMachine.transition("success", "cancelled")

    def test_worker_skips_cancelled_task(self):
        """cancel 生效：worker 不执行已取消任务（迁移校验拒绝）。"""

        async def run():
            init_db()
            db = SessionLocal()
            try:
                uid = uuid.uuid4().hex[:8]
                repo = EvalRepo(db)
                task = repo.create_task(
                    name=f"cancelled-task-{uid}",
                    dataset_id=1,
                    models=["m1"],
                    config={},
                    status="cancelled",
                    progress=0.0,
                    created_by="qa",
                )
                worker = EvalWorker()
                with pytest.raises(ConflictError):
                    await worker.process_task(task.id, {"dataset_id": 1, "models": ["m1"]})
            finally:
                db.close()

        import asyncio

        asyncio.run(run())

    def test_queue_retry_reenqueue_and_max(self):
        """队列失败重试：未达上限重新入队；达到 MAX_RETRIES 停止。"""
        q = EvalQueue(redis_client=None)
        assert q.retry(1, {"x": 1}, attempt=1) is True
        assert q.size() == 1
        assert q.retry(2, {}, attempt=3) is False

    def test_request_level_timeout_exists(self):
        """超时控制（请求级）：request_timeout_s > 0 且适配器使用该超时。"""
        settings = get_settings()
        assert settings.request_timeout_s > 0
        from llm_platform.adapters.openai_adapter import OpenAIAdapter

        adapter = OpenAIAdapter()
        assert adapter.timeout_s > 0


# ═══════════════════════════════════════════════════════════
# E. eval:rl: 限流隔离
# ═══════════════════════════════════════════════════════════


class TestEvalBucketIsolation:
    """评测独立桶与在线桶互不影响（ADR-003）。"""

    def test_online_exhausted_eval_still_works(self):
        """在线桶耗尽后，评测桶仍可执行（反之亦然不共用）。"""
        online = TokenBucket(redis_client=None, prefix="rl")
        eval_b = TokenBucket(redis_client=None, prefix="eval:rl")
        subject = f"iso-{uuid.uuid4().hex[:6]}"

        # 耗尽在线桶（rpm=2）
        online.check("key", subject, rpm_limit=2, tpm_limit=100000)
        online.check("key", subject, rpm_limit=2, tpm_limit=100000)
        with pytest.raises(RateLimitError):
            online.check("key", subject, rpm_limit=2, tpm_limit=100000)

        # 评测桶不受影响
        eval_b.check("key", subject, rpm_limit=5, tpm_limit=100000)
        eval_b.check("key", subject, rpm_limit=5, tpm_limit=100000)

    def test_eval_exhausted_online_still_works(self):
        """评测桶耗尽后，在线桶仍可执行。"""
        online = TokenBucket(redis_client=None, prefix="rl")
        eval_b = TokenBucket(redis_client=None, prefix="eval:rl")
        subject = f"iso2-{uuid.uuid4().hex[:6]}"

        eval_b.check("key", subject, rpm_limit=1, tpm_limit=100000)
        with pytest.raises(RateLimitError):
            eval_b.check("key", subject, rpm_limit=1, tpm_limit=100000)

        # 在线桶不受影响
        online.check("key", subject, rpm_limit=5, tpm_limit=100000)

    def test_eval_runner_uses_eval_bucket_prefix(self):
        """EvalRunner 必须使用 eval:rl 独立桶前缀。"""
        init_db()
        db = SessionLocal()
        try:
            runner = EvalRunner(db, redis_client=None)
            assert runner.eval_bucket.prefix == "eval:rl"
            # 在线桶前缀不同（gateway/service.py: prefix="rl"）
            assert runner.eval_bucket.prefix != "rl"
        finally:
            db.close()


# ═══════════════════════════════════════════════════════════
# F. MCP 工具网关审计
# ═══════════════════════════════════════════════════════════


class TestMcpAudit:
    """MCP 调用 source=mcp 落 call_log；未知工具错误一致。"""

    def test_mcp_call_logged_with_source_mcp(self):
        """工具调用落 call_log 且 source=mcp、model=mcp:<tool>。"""
        init_db()
        db = SessionLocal()
        try:
            gw = McpGateway(db)
            uid = uuid.uuid4().hex[:8]
            tool_name = f"audit-echo-{uid}"
            gw.register_tool(
                name=tool_name,
                schema={"handler": "echo", "input_schema": {}},
            )
            result = gw.call_tool(tool_name, {"message": "hi"}, tenant_id=1, project_id=1)
            assert result["success"] is True

            total, items = CallLogRepo(db).query_calls(
                model=f"mcp:{tool_name}", source="mcp"
            )
            assert total == 1, "call_log 中必须有 source=mcp 的记录"
            record = items[0]
            assert record.source == "mcp"
            assert record.model == f"mcp:{tool_name}"
            assert record.latency_ms >= 0
            assert record.status == "ok"
            assert record.prompt_hash  # 审计指纹
        finally:
            db.close()

    def test_mcp_failed_call_logged_with_error_status(self):
        """失败调用同样落 call_log（status=error）。"""
        init_db()
        db = SessionLocal()
        try:
            gw = McpGateway(db)
            uid = uuid.uuid4().hex[:8]
            tool_name = f"bad-handler-{uid}"
            gw.register_tool(
                name=tool_name,
                schema={"handler": "not_registered", "input_schema": {}},
            )
            result = gw.call_tool(tool_name, {}, tenant_id=1, project_id=1)
            assert result["success"] is False

            total, items = CallLogRepo(db).query_calls(
                model=f"mcp:{tool_name}", source="mcp"
            )
            assert total == 1
            assert items[0].status == "error"
            assert items[0].error_code == "UPSTREAM_004"
        finally:
            db.close()

    def test_mcp_unknown_tool_structured_error(self):
        """未知工具返回结构化错误（success=False + 明确 error）。"""
        init_db()
        db = SessionLocal()
        try:
            gw = McpGateway(db)
            result = gw.call_tool(f"no-such-{uuid.uuid4().hex[:4]}", {}, tenant_id=1, project_id=1)
            assert result["success"] is False
            assert "不存在" in result["error"]
            assert result["result"] is None
        finally:
            db.close()

    def test_mcp_example_tools_work(self):
        """examples/mcp_tools 两个示例工具（echo/search）可执行。"""
        import importlib.util

        root = Path(__file__).resolve().parents[2]
        spec = importlib.util.spec_from_file_location(
            "echo_tool", root / "examples" / "mcp_tools" / "echo_tool.py"
        )
        echo_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(echo_mod)
        assert echo_mod.handle_echo({"message": "hi"})["echo"] == "hi"
        assert echo_mod.TOOL_SCHEMA["handler"] == "echo"

        spec2 = importlib.util.spec_from_file_location(
            "search_tool", root / "examples" / "mcp_tools" / "search_tool.py"
        )
        search_mod = importlib.util.module_from_spec(spec2)
        spec2.loader.exec_module(search_mod)
        out = search_mod.handle_search({"query": "test"})
        assert len(out["results"]) == 3
        assert search_mod.TOOL_SCHEMA["handler"] == "search"


# ═══════════════════════════════════════════════════════════
# G. SRC-1 / SRC-2 回归
# ═══════════════════════════════════════════════════════════


class TestM1LeftoverFixes:
    """M1 遗留修复回归：SRC-1 fail-closed、SRC-2 流式 call_log。"""

    def test_src1_create_token_empty_secret_fail_closed(self):
        """SRC-1：空 jwt_secret 时 create_access_token 必须拒绝（AuthError）。"""
        settings = get_settings()
        original = settings.jwt_secret
        settings.jwt_secret = ""
        try:
            with pytest.raises(AuthError) as exc:
                create_access_token(
                    subject="1", tenant_id=1, role="admin", ttl_minutes=5
                )
            assert exc.value.code == "AUTH_001"
        finally:
            settings.jwt_secret = original

    def test_src1_decode_token_empty_secret_fail_closed(self):
        """对照：decode 同样 fail-closed。"""
        settings = get_settings()
        original = settings.jwt_secret
        settings.jwt_secret = ""
        try:
            from llm_platform.auth.jwt import decode_access_token

            with pytest.raises(AuthError):
                decode_access_token("some.token.value")
        finally:
            settings.jwt_secret = original

    def test_src2_stream_call_log_recorded(self, client, qa2_setup_data):
        """SRC-2：流式调用后 call_log 有记录，含 trace_id/tokens_estimated/first_token_latency。"""
        captured: list[dict] = []

        def capture_put(self, record):  # noqa: ANN001
            captured.append(record)

        chunks = [
            StreamChunk(
                id="c1",
                object="chat.completion.chunk",
                created=1736900000,
                model=qa2_setup_data["model_name"],
                choices=[{"index": 0, "delta": {"role": "assistant", "content": "你"}, "finish_reason": None}],
            ),
            StreamChunk(
                id="c1",
                object="chat.completion.chunk",
                created=1736900000,
                model=qa2_setup_data["model_name"],
                choices=[{"index": 0, "delta": {}, "finish_reason": "stop"}],
                usage=Usage(prompt_tokens=10, completion_tokens=5),
            ),
        ]

        async def fake_chat_stream(self, request, *, base_url, api_key):  # noqa: ANN001
            for chunk in chunks:
                yield chunk

        from unittest.mock import patch

        with patch.object(CallLogBuffer, "put", capture_put), patch(
            "llm_platform.adapters.openai_adapter.OpenAIAdapter.chat_stream",
            fake_chat_stream,
        ):
            resp = client.post(
                "/v1/chat/completions",
                json={
                    "model": qa2_setup_data["model_name"],
                    "messages": [{"role": "user", "content": "src2 回归"}],
                    "stream": True,
                },
                headers={"Authorization": f"Bearer {qa2_setup_data['key_plaintext']}"},
            )

        assert resp.status_code == 200
        assert captured, "流式 call_log 未落库（SRC-2 未修复？）"
        record = captured[-1]
        assert record["trace_id"], "call_log 缺 trace_id"
        assert "tokens_estimated" in record, "call_log 缺 tokens_estimated 标记"
        # 上游提供了 usage → 不是估算
        assert record["tokens_estimated"] is False
        assert record["prompt_tokens"] == 10
        assert record["completion_tokens"] == 5
        # 首 token 延迟必须记录
        assert record["first_token_latency_ms"] is not None
        assert record["first_token_latency_ms"] >= 0
        # trace_id 与响应头一致（同一请求链路）
        assert record["trace_id"] == resp.headers.get("x-request-id")

    def test_src2_stream_estimated_tokens_flagged(self, client, qa2_setup_data):
        """SRC-2：上游未给 usage 时 tokens_estimated=True（估算标记）。"""
        captured: list[dict] = []

        def capture_put(self, record):  # noqa: ANN001
            captured.append(record)

        chunks = [
            StreamChunk(
                id="c2",
                object="chat.completion.chunk",
                created=1736900000,
                model=qa2_setup_data["model_name"],
                choices=[{"index": 0, "delta": {"role": "assistant", "content": "hello"}, "finish_reason": None}],
            ),
        ]

        async def fake_chat_stream(self, request, *, base_url, api_key):  # noqa: ANN001
            for chunk in chunks:
                yield chunk

        from unittest.mock import patch

        with patch.object(CallLogBuffer, "put", capture_put), patch(
            "llm_platform.adapters.openai_adapter.OpenAIAdapter.chat_stream",
            fake_chat_stream,
        ):
            resp = client.post(
                "/v1/chat/completions",
                json={
                    "model": qa2_setup_data["model_name"],
                    "messages": [{"role": "user", "content": "estimate"}],
                    "stream": True,
                },
                headers={"Authorization": f"Bearer {qa2_setup_data['key_plaintext']}"},
            )

        assert resp.status_code == 200
        assert captured
        record = captured[-1]
        assert record["tokens_estimated"] is True
        assert record["trace_id"]


# ═══════════════════════════════════════════════════════════
# H. 评估端到端（工程师冒烟流程复跑）
# ═══════════════════════════════════════════════════════════


class TestEvalE2E:
    """基准集注册 → 评分 → 报告 → 对比 全流程。"""

    def test_full_eval_flow(self):
        init_db()
        db = SessionLocal()
        try:
            items = [
                {"input_prompt": "1+1=?", "expected_output": "2"},
                {"input_prompt": "中国的首都是？", "expected_output": "北京"},
                {"input_prompt": "水的化学式是？", "expected_output": "H2O"},
                {"input_prompt": "太阳系最大的行星是？", "expected_output": "木星"},
                {"input_prompt": "HTTP 404 表示？", "expected_output": "未找到"},
            ]
            mgr = DatasetManager(db)
            uid = uuid.uuid4().hex[:8]
            result = mgr.register(f"e2e-ds-{uid}", f"1.0.{uid[:4]}", items)
            assert result["item_count"] == 5

            preds = ["2", "北京", "H2O", "木星", "404 Not Found"]
            refs = [it["expected_output"] for it in items]
            metrics = compute_metrics(
                preds,
                refs,
                latencies_ms=[100] * 5,
                usage_list=[{"prompt_tokens": 10, "completion_tokens": 5}] * 5,
                costs=[0.001] * 5,
            )
            score = overall_score(metrics)
            assert 0.0 <= score <= 1.0
            assert metrics["accuracy"] == pytest.approx(0.8)
            assert metrics["tokens"] == 75
            assert metrics["cost_usd"] == pytest.approx(0.005)

            gen = ReportGenerator()
            report = gen.generate(1, "test-model", metrics, score, result["version"])
            comparison = gen.compare(
                [report, {**report, "model": "model-b", "overall_score": score * 0.8}]
            )
            assert comparison["conclusion"]["winner"] == "test-model"
            assert "accuracy" in comparison["dimensions"]
            assert len(comparison["series"]) == 2
            # diff 可归因到指标维度
            assert "accuracy" in comparison["conclusion"]["diff"]
        finally:
            db.close()

    def test_compare_api_winner(self, client, eval_jwt):
        """对比分析 API：winner 判定正确。"""
        init_db()
        db = SessionLocal()
        try:
            uid = uuid.uuid4().hex[:8]
            repo = EvalRepo(db)
            task = repo.create_task(
                name=f"cmp-task-{uid}",
                dataset_id=1,
                models=["model-a", "model-b"],
                config={},
                status="pending",
                progress=0.0,
                created_by="qa",
            )
            repo.create_report(
                task_id=task.id,
                model="model-a",
                overall_score=0.9,
                metrics={"accuracy": 0.9, "f1": 0.8},
                dataset_version="1.0.0",
                evaluator_version="v1",
            )
            repo.create_report(
                task_id=task.id,
                model="model-b",
                overall_score=0.7,
                metrics={"accuracy": 0.7, "f1": 0.6},
                dataset_version="1.0.0",
                evaluator_version="v1",
            )
        finally:
            db.close()

        resp = client.get(
            f"/admin/v1/eval-reports/compare?task_id={task.id}",
            headers=eval_jwt,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["conclusion"]["winner"] == "model-a"
        assert len(body["series"]) == 2
        assert "accuracy" in body["dimensions"]

    def test_export_json_api(self, client, eval_jwt):
        """报告导出 JSON API。"""
        init_db()
        db = SessionLocal()
        try:
            uid = uuid.uuid4().hex[:8]
            repo = EvalRepo(db)
            task = repo.create_task(
                name=f"exp-task-{uid}",
                dataset_id=1,
                models=["m1"],
                config={},
                status="pending",
                progress=0.0,
                created_by="qa",
            )
            report = repo.create_report(
                task_id=task.id,
                model="m1",
                overall_score=0.5,
                metrics={"accuracy": 0.5},
                dataset_version="1.0.0",
                evaluator_version="v1",
            )
            report_id = report.id
        finally:
            db.close()

        resp = client.get(
            f"/admin/v1/eval-reports/{report_id}/export?format=json",
            headers=eval_jwt,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["format"] == "json"
        content = json.loads(body["content"])
        assert content["model"] == "m1"


# ═══════════════════════════════════════════════════════════
# I. P1-02 固定基准集（内置种子）
# ═══════════════════════════════════════════════════════════


class TestP102FixedBenchmark:
    """P1-02：固定基准集（≥200 条）内置交付核查。"""

    def test_builtin_seed_dataset_present(self):
        """固定基准集（≥200 条）应内置（种子函数或随仓数据文件）。

        依据：
        - PRD P1-02 "自动化测试用例与基准集：固定基准集（≥200 条）+ 自定义用例上传"
        - stage-2-report.md §1 声称 datasets.py 含"内置种子"
        - datasets.py:1 docstring 声称"内置种子（P1-02）"
        """
        from llm_platform.eval import datasets as ds_mod

        seed_fn_names = (
            "seed",
            "seed_builtin",
            "load_seed",
            "load_builtin",
            "seed_dataset",
            "register_builtin",
        )
        has_seed_fn = any(
            callable(getattr(ds_mod, n, None)) for n in seed_fn_names
        ) or any(callable(getattr(DatasetManager, n, None)) for n in seed_fn_names)

        # 随仓种子数据文件（≥200 条评测样本）
        root = Path(__file__).resolve().parents[2]
        found_seed_file = False
        candidates = list(root.joinpath("data").rglob("*.json")) + list(
            root.joinpath("src", "llm_platform", "eval").rglob("*.json")
        )
        for f in candidates:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            items = data if isinstance(data, list) else data.get("items", [])
            if (
                isinstance(items, list)
                and len(items) >= 200
                and all(
                    isinstance(it, dict) and "input_prompt" in it for it in items[:5]
                )
            ):
                found_seed_file = True
                break

        assert has_seed_fn or found_seed_file, (
            "未交付内置固定基准集：datasets.py:1 声称'内置种子'但无种子加载函数，"
            "data/ 与 eval 模块下无 ≥200 条评测样本文件（PRD P1-02：固定基准集 ≥200 条）"
        )
