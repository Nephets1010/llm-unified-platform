"""动态路由模块：策略选路 + 熔断 failover。"""

from .health import CircuitBreaker, HealthRegistry
from .router import Router
from .strategies import (
    CostFirstStrategy,
    HealthAwareStrategy,
    LatencyFirstStrategy,
    RouteStrategy,
    WeightedRoundRobinStrategy,
    get_strategy,
)

__all__ = [
    "CircuitBreaker",
    "HealthRegistry",
    "RouteStrategy",
    "CostFirstStrategy",
    "LatencyFirstStrategy",
    "WeightedRoundRobinStrategy",
    "HealthAwareStrategy",
    "get_strategy",
    "Router",
]
