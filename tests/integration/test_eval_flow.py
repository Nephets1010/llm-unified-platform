"""评测端到端集成测试（P1-01~P1-06）：任务提交→执行→报告。"""

from __future__ import annotations

import uuid

import pytest

from llm_platform.db.session import SessionLocal, init_db
from llm_platform.eval.datasets import DatasetManager
from llm_platform.eval.metrics import compute_metrics
from llm_platform.scheduler.queue import EvalQueue
from llm_platform.scheduler.state_machine import TaskStateMachine


@pytest.fixture(autouse=True)
def setup_db():
    init_db()
    yield


class TestDatasetManagement:
    def test_register_dataset(self):
        db = SessionLocal()
        try:
            mgr = DatasetManager(db)
            uid = uuid.uuid4().hex[:8]
            items = [
                {"input_prompt": f"问题{i}", "expected_output": f"答案{i}", "metadata": {"cat": "test"}}
                for i in range(5)
            ]
            result = mgr.register(f"test-ds-{uid}", f"1.0.{uid[:4]}", items)
            assert result["item_count"] == 5
            assert len(result["checksum"]) == 64
        finally:
            db.close()

    def test_checksum_consistency(self):
        db = SessionLocal()
        try:
            mgr = DatasetManager(db)
            uid = uuid.uuid4().hex[:8]
            items = [{"input_prompt": "a", "expected_output": "b"}]
            result = mgr.register(f"test-cs-{uid}", f"1.0.{uid[:4]}", items)
            assert mgr.verify_checksum(result["id"], items) is True
            assert mgr.verify_checksum(result["id"], [{"input_prompt": "x", "expected_output": "y"}]) is False
        finally:
            db.close()

    def test_get_items(self):
        db = SessionLocal()
        try:
            mgr = DatasetManager(db)
            uid = uuid.uuid4().hex[:8]
            items = [{"input_prompt": "p1", "expected_output": "r1"}, {"input_prompt": "p2", "expected_output": "r2"}]
            result = mgr.register(f"test-gi-{uid}", f"1.0.{uid[:4]}", items)
            fetched = mgr.get_items(result["id"])
            assert len(fetched) == 2
            assert fetched[0]["input_prompt"] == "p1"
        finally:
            db.close()


class TestTaskStateMachine:
    def test_valid_transitions(self):
        assert TaskStateMachine.validate_transition("pending", "running") is True
        assert TaskStateMachine.validate_transition("running", "success") is True
        assert TaskStateMachine.validate_transition("running", "failed") is True
        assert TaskStateMachine.validate_transition("pending", "cancelled") is True

    def test_invalid_transitions(self):
        assert TaskStateMachine.validate_transition("success", "running") is False
        assert TaskStateMachine.validate_transition("cancelled", "running") is False
        assert TaskStateMachine.validate_transition("pending", "success") is False

    def test_transition_raises_on_invalid(self):
        from llm_platform.errors import ConflictError

        with pytest.raises(ConflictError):
            TaskStateMachine.transition("success", "running")

    def test_is_terminal(self):
        assert TaskStateMachine.is_terminal("success") is True
        assert TaskStateMachine.is_terminal("cancelled") is True
        assert TaskStateMachine.is_terminal("running") is False
        assert TaskStateMachine.is_terminal("pending") is False
        # failed 允许重试（→pending），所以不是终态
        assert TaskStateMachine.is_terminal("failed") is False


class TestEvalQueue:
    def test_enqueue_dequeue(self):
        q = EvalQueue(redis_client=None)
        q.enqueue(1, {"dataset_id": 1, "models": ["m1"]})
        assert q.size() == 1
        item = q.dequeue()
        assert item["task_id"] == 1
        assert q.size() == 0

    def test_retry_max_retries(self):
        q = EvalQueue(redis_client=None)
        # 第 3 次 retry 应失败（MAX_RETRIES=3）
        assert q.retry(1, {}, attempt=3) is False

    def test_retry_reenqueue(self):
        q = EvalQueue(redis_client=None)
        assert q.retry(1, {}, attempt=1) is True
        assert q.size() == 1


class TestMetricsReproducibility:
    def test_metrics_reproducible(self):
        """P1-02：重复跑分波动 ≤ 2%（纯函数 → 精确一致）。"""
        kwargs = dict(
            predictions=["答案A", "答案B", "答案C"],
            references=["答案A", "答案B", "答案D"],
            latencies_ms=[100, 200, 150],
            usage_list=[{"prompt_tokens": 10, "completion_tokens": 5}] * 3,
            costs=[0.001] * 3,
        )
        r1 = compute_metrics(**kwargs)
        r2 = compute_metrics(**kwargs)
        assert r1 == r2
        assert r1["accuracy"] == pytest.approx(2 / 3, abs=0.001)


class TestReportComparison:
    def test_compare_models(self):
        from llm_platform.eval.report import ReportGenerator

        gen = ReportGenerator()
        reports = [
            {"model": "model-a", "overall_score": 0.85, "metrics": {"accuracy": 0.9, "f1": 0.8}},
            {"model": "model-b", "overall_score": 0.75, "metrics": {"accuracy": 0.7, "f1": 0.75}},
        ]
        result = gen.compare(reports)
        assert result["conclusion"]["winner"] == "model-a"
        assert "accuracy" in result["dimensions"]
        assert len(result["series"]) == 2

    def test_export_json(self):
        from llm_platform.eval.report import ReportGenerator

        gen = ReportGenerator()
        report = {"model": "test", "overall_score": 0.9, "metrics": {"accuracy": 1.0}}
        json_str = gen.export_json(report)
        assert "test" in json_str
        assert "accuracy" in json_str
