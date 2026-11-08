"""限流测试：令牌桶、预算熔断、降级本地桶。"""

from __future__ import annotations

import pytest

from llm_platform.errors import QuotaExceededError, RateLimitError
from llm_platform.ratelimit.budget import BudgetGuard
from llm_platform.ratelimit.token_bucket import LocalTokenBucket, TokenBucket


class TestLocalTokenBucket:
    def test_consume_within_limit(self):
        bucket = LocalTokenBucket(max_tokens=10, refill_rate=1.0)
        result = bucket.try_consume(5)
        assert result.allowed is True
        assert result.remaining == 5

    def test_consume_exceeds_limit(self):
        bucket = LocalTokenBucket(max_tokens=5, refill_rate=1.0)
        bucket.try_consume(5)
        result = bucket.try_consume(1)
        assert result.allowed is False
        assert result.retry_after > 0

    def test_refill(self):
        import time
        bucket = LocalTokenBucket(max_tokens=10, refill_rate=100.0)
        bucket.try_consume(10)
        time.sleep(0.1)  # 应补充约 10 个令牌
        result = bucket.try_consume(5)
        assert result.allowed is True


class TestTokenBucket:
    def test_rpm_limit_local(self):
        bucket = TokenBucket(redis_client=None, prefix="rl")
        for _ in range(60):
            bucket.check("key", "test-1", rpm_limit=60, tpm_limit=60000)
        with pytest.raises(RateLimitError):
            bucket.check("key", "test-1", rpm_limit=60, tpm_limit=60000)

    def test_tpm_limit_local(self):
        bucket = TokenBucket(redis_client=None, prefix="rl")
        # 第一次大 token 消耗
        bucket.check("key", "test-2", rpm_limit=100, tpm_limit=100, token_cost=90)
        with pytest.raises(RateLimitError):
            bucket.check("key", "test-2", rpm_limit=100, tpm_limit=100, token_cost=50)

    def test_eval_bucket_isolated(self):
        """评测桶与在线桶物理隔离（前缀不同）。"""
        online_bucket = TokenBucket(redis_client=None, prefix="rl")
        eval_bucket = TokenBucket(redis_client=None, prefix="eval:rl")
        # 在线桶用尽
        for _ in range(60):
            online_bucket.check("key", "shared", rpm_limit=60, tpm_limit=60000)
        with pytest.raises(RateLimitError):
            online_bucket.check("key", "shared", rpm_limit=60, tpm_limit=60000)
        # 评测桶不受影响
        result = eval_bucket.check("key", "shared", rpm_limit=60, tpm_limit=60000)
        assert result.allowed is True


class TestBudgetGuard:
    def test_within_budget(self):
        guard = BudgetGuard(redis_client=None)
        guard.record_cost(1, 50.0)
        ratio = guard.check_budget(1, monthly_budget=100.0, alert_ratio=0.8)
        assert ratio == 0.5

    def test_budget_alert_80(self):
        guard = BudgetGuard(redis_client=None)
        guard.record_cost(2, 85.0)
        ratio = guard.check_budget(2, monthly_budget=100.0, alert_ratio=0.8)
        assert ratio >= 0.8

    def test_budget_exceeded(self):
        guard = BudgetGuard(redis_client=None)
        guard.record_cost(3, 100.0)
        with pytest.raises(QuotaExceededError):
            guard.check_budget(3, monthly_budget=100.0, alert_ratio=0.8)

    def test_reset_month(self):
        guard = BudgetGuard(redis_client=None)
        guard.record_cost(4, 50.0)
        guard.reset_month(4)
        assert guard.get_used(4) == 0.0
