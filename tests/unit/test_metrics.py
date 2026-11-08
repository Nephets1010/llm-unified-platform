"""指标纯函数可重算验证（P1-01）。"""

from __future__ import annotations

import pytest

from llm_platform.eval.metrics import (
    accuracy,
    compute_metrics,
    f1_score,
    latency_p95,
    overall_score,
    rouge_l,
    tool_success_rate,
    total_cost,
    total_tokens,
)


class TestAccuracy:
    def test_perfect_match(self):
        assert accuracy(["hello", "world"], ["hello", "world"]) == 1.0

    def test_partial_match(self):
        assert accuracy(["hello", "foo"], ["hello", "world"]) == 0.5

    def test_no_match(self):
        assert accuracy(["foo", "bar"], ["hello", "world"]) == 0.0

    def test_empty(self):
        assert accuracy([], []) == 0.0


class TestF1Score:
    def test_exact_match(self):
        val = f1_score(["hello world"], ["hello world"])
        assert val == 1.0

    def test_partial_overlap(self):
        val = f1_score(["hello world"], ["hello there"])
        assert 0 < val < 1.0

    def test_no_overlap(self):
        val = f1_score(["foo bar"], ["hello world"])
        assert val == 0.0


class TestRougeL:
    def test_exact_match(self):
        assert rouge_l(["hello world"], ["hello world"]) == 1.0

    def test_partial(self):
        val = rouge_l(["hello world"], ["hello there world"])
        assert 0 < val < 1.0

    def test_empty(self):
        assert rouge_l([], []) == 0.0


class TestLatency:
    def test_p95(self):
        latencies = list(range(1, 101))  # 1~100
        val = latency_p95(latencies)
        assert val == 95  # P95 of 100 values = 95th

    def test_empty(self):
        assert latency_p95([]) == 0.0


class TestTokenAndCost:
    def test_total_tokens(self):
        usage = [
            {"prompt_tokens": 10, "completion_tokens": 5},
            {"prompt_tokens": 20, "completion_tokens": 10},
        ]
        assert total_tokens(usage) == 45

    def test_total_cost(self):
        assert total_cost([0.1, 0.2, 0.3]) == pytest.approx(0.6)


class TestToolSuccessRate:
    def test_full_success(self):
        assert tool_success_rate([True, True, True]) == 1.0

    def test_partial(self):
        assert tool_success_rate([True, False, True]) == pytest.approx(2 / 3)

    def test_empty(self):
        assert tool_success_rate([]) == 0.0


class TestComputeMetrics:
    def test_full_computation(self):
        metrics = compute_metrics(
            predictions=["hello world", "foo bar"],
            references=["hello world", "foo baz"],
            latencies_ms=[100, 200],
            usage_list=[{"prompt_tokens": 10, "completion_tokens": 5}] * 2,
            costs=[0.001, 0.002],
        )
        assert "accuracy" in metrics
        assert "f1" in metrics
        assert "rouge_l" in metrics
        assert "latency_p95_ms" in metrics
        assert metrics["tokens"] == 30
        assert metrics["cost_usd"] == pytest.approx(0.003, abs=0.0001)

    def test_reproducible(self):
        """指标可重算：同样输入 → 同样输出。"""
        args = dict(
            predictions=["hello", "world"],
            references=["hello", "there"],
            latencies_ms=[10, 20],
            usage_list=[{"prompt_tokens": 5, "completion_tokens": 3}] * 2,
            costs=[0.1, 0.2],
        )
        r1 = compute_metrics(**args)
        r2 = compute_metrics(**args)
        assert r1 == r2

    def test_selective_metrics(self):
        metrics = compute_metrics(
            predictions=["a"], references=["a"],
            metric_names=["accuracy", "f1"],
        )
        assert "accuracy" in metrics
        assert "f1" in metrics
        assert "rouge_l" not in metrics


class TestOverallScore:
    def test_perfect_score(self):
        metrics = {"accuracy": 1.0, "f1": 1.0, "rouge_l": 1.0}
        assert overall_score(metrics) == pytest.approx(1.0)

    def test_weighted_score(self):
        metrics = {"accuracy": 1.0, "f1": 0.0, "rouge_l": 0.0}
        score = overall_score(metrics)
        assert 0 < score < 1.0

    def test_empty(self):
        assert overall_score({}) == 0.0
