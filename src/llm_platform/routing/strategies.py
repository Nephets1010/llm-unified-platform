"""路由策略：cost_first / latency_first / weighted_round_robin / health_aware。

策略只暴露 RouteStrategy 协议，不感知厂商差异（ADR-002）。
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod

from ..models.entities import ModelEndpoint
from .health import get_health_registry


class RouteStrategy(ABC):
    """路由策略协议。"""

    name: str = "base"

    @abstractmethod
    def select(self, endpoints: list[ModelEndpoint]) -> ModelEndpoint | None:
        """从候选端点池中选择一个。"""
        raise NotImplementedError


class CostFirstStrategy(RouteStrategy):
    """成本优先：选 cost_per_1k_prompt 最低的端点。"""

    name = "cost_first"

    def select(self, endpoints: list[ModelEndpoint]) -> ModelEndpoint | None:
        if not endpoints:
            return None
        return min(endpoints, key=lambda e: e.cost_per_1k_prompt + e.cost_per_1k_completion)


class LatencyFirstStrategy(RouteStrategy):
    """延迟优先：选 avg_latency_ms 最低的端点。"""

    name = "latency_first"

    def select(self, endpoints: list[ModelEndpoint]) -> ModelEndpoint | None:
        if not endpoints:
            return None
        return min(endpoints, key=lambda e: e.avg_latency_ms)


class WeightedRoundRobinStrategy(RouteStrategy):
    """加权轮询：按 weight 概率选择。"""

    name = "weighted"

    def select(self, endpoints: list[ModelEndpoint]) -> ModelEndpoint | None:
        if not endpoints:
            return None
        weights = [max(e.weight, 0.01) for e in endpoints]
        return random.choices(endpoints, weights=weights, k=1)[0]


class HealthAwareStrategy(RouteStrategy):
    """健康度优先：过滤熔断不可用端点，按错误率+延迟综合评分。"""

    name = "health_aware"

    def select(self, endpoints: list[ModelEndpoint]) -> ModelEndpoint | None:
        registry = get_health_registry()
        available = [e for e in endpoints if registry.is_available(e.id)]
        if not available:
            return None

        def score(e: ModelEndpoint) -> float:
            h = registry.get_health(e.id)
            # 错误率权重 0.7 + 延迟权重 0.3（归一化）
            error_score = 1.0 - min(h.error_rate, 1.0)
            latency_score = 1.0 / (1.0 + h.avg_latency / 1000.0)
            return 0.7 * error_score + 0.3 * latency_score

        return max(available, key=score)


_STRATEGIES: dict[str, RouteStrategy] = {
    "cost_first": CostFirstStrategy(),
    "latency_first": LatencyFirstStrategy(),
    "weighted": WeightedRoundRobinStrategy(),
    "health_aware": HealthAwareStrategy(),
}


def get_strategy(name: str) -> RouteStrategy:
    """按名称获取路由策略，默认 health_aware。"""
    return _STRATEGIES.get(name, _STRATEGIES["health_aware"])
