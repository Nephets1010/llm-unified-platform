"""路由测试：四策略、熔断 failover、健康度。"""

from __future__ import annotations

import pytest

from llm_platform.errors import NoRouteError
from llm_platform.models.entities import ModelEndpoint
from llm_platform.routing.health import (
    CircuitState,
    HealthRegistry,
)
from llm_platform.routing.router import Router
from llm_platform.routing.strategies import (
    CostFirstStrategy,
    HealthAwareStrategy,
    LatencyFirstStrategy,
    WeightedRoundRobinStrategy,
    get_strategy,
)


def _make_endpoint(id: int, cost: float = 0.001, latency: float = 100.0, weight: float = 1.0, priority: int = 100) -> ModelEndpoint:
    return ModelEndpoint(
        id=id,
        provider="openai",
        protocol="openai",
        model_name="test-model",
        base_url="http://test",
        credential_env="TEST_KEY",
        enabled=True,
        healthy=True,
        weight=weight,
        priority=priority,
        cost_per_1k_prompt=cost,
        cost_per_1k_completion=cost,
        avg_latency_ms=latency,
    )


class TestRouteStrategies:
    def test_cost_first(self):
        endpoints = [_make_endpoint(1, cost=0.01), _make_endpoint(2, cost=0.001)]
        result = CostFirstStrategy().select(endpoints)
        assert result.id == 2

    def test_latency_first(self):
        endpoints = [_make_endpoint(1, latency=500), _make_endpoint(2, latency=50)]
        result = LatencyFirstStrategy().select(endpoints)
        assert result.id == 2

    def test_weighted_distributed(self):
        endpoints = [_make_endpoint(1, weight=9.0), _make_endpoint(2, weight=1.0)]
        strategy = WeightedRoundRobinStrategy()
        counts = {1: 0, 2: 0}
        for _ in range(1000):
            r = strategy.select(endpoints)
            counts[r.id] += 1
        assert counts[1] > counts[2]  # 高权重端点被选中更多

    def test_health_aware(self):
        endpoints = [_make_endpoint(1), _make_endpoint(2)]
        result = HealthAwareStrategy().select(endpoints)
        assert result is not None

    def test_get_strategy(self):
        assert get_strategy("cost_first").name == "cost_first"
        assert get_strategy("unknown").name == "health_aware"  # 默认


class TestCircuitBreaker:
    def test_closed_to_open(self):
        registry = HealthRegistry()
        registry.configure(error_rate_threshold=0.5, open_seconds=0.1)
        breaker = registry.get_breaker(1)
        assert breaker.state == CircuitState.CLOSED

        # 连续失败触发熔断
        for _ in range(10):
            registry.record_failure(1)

        assert breaker.state == CircuitState.OPEN

    def test_open_to_half_open(self):
        registry = HealthRegistry()
        registry.configure(error_rate_threshold=0.5, open_seconds=0.01)
        breaker = registry.get_breaker(2)
        registry.record_failure(2)
        for _ in range(10):
            registry.record_failure(2)

        # 等待 open_seconds 过期
        import time
        time.sleep(0.02)
        assert breaker.is_available()  # 应转 HALF_OPEN
        assert breaker.state == CircuitState.HALF_OPEN

    def test_half_open_to_closed(self):
        registry = HealthRegistry()
        registry.configure(error_rate_threshold=0.5, open_seconds=0.01, half_open_probes=1)
        breaker = registry.get_breaker(3)
        for _ in range(10):
            registry.record_failure(3)
        import time
        time.sleep(0.02)
        registry.record_success(3, 100)  # 半开探测成功
        assert breaker.state == CircuitState.CLOSED


class TestRouter:
    def test_select_endpoint(self):
        router = Router()
        endpoints = [_make_endpoint(1), _make_endpoint(2)]
        result = router.select_endpoint(endpoints)
        assert result is not None

    def test_no_candidates(self):
        router = Router()
        with pytest.raises(NoRouteError):
            router.select_endpoint([])

    def test_order_candidates(self):
        router = Router()
        endpoints = [_make_endpoint(1, priority=100), _make_endpoint(2, priority=10)]
        ordered = router.order_candidates(endpoints)
        assert ordered[0].id == 2  # 低 priority 先
