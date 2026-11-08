"""健康度滑动窗口 + 熔断器三态（closed → open → half_open）。

健康度由错误率 + 延迟 EWMA 维护；熔断器 open 持续 30s 后半开探测。
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum

from ..logging import get_logger

logger = get_logger("routing.health")


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class EndpointHealth:
    """单端点健康度快照。"""

    endpoint_id: int
    window: deque = field(default_factory=lambda: deque(maxlen=100))
    latency_ewma: float = 0.0
    total_requests: int = 0
    total_errors: int = 0

    @property
    def error_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.total_errors / self.total_requests

    @property
    def avg_latency(self) -> float:
        return self.latency_ewma

    def record_success(self, latency_ms: float) -> None:
        self.window.append(True)
        self.total_requests += 1
        alpha = 0.3
        self.latency_ewma = alpha * latency_ms + (1 - alpha) * self.latency_ewma

    def record_failure(self) -> None:
        self.window.append(False)
        self.total_requests += 1
        self.total_errors += 1


@dataclass
class CircuitBreaker:
    """熔断器三态。"""

    endpoint_id: int
    state: CircuitState = CircuitState.CLOSED
    error_rate_threshold: float = 0.5
    open_seconds: float = 30.0
    half_open_probes: int = 1
    opened_at: float = 0.0
    half_open_successes: int = 0
    half_open_failures: int = 0
    _registry: HealthRegistry | None = None  # 反向引用父注册表

    def record_result(self, success: bool) -> None:
        """记录调用结果并驱动状态转换。"""
        if self.state == CircuitState.OPEN:
            # OPEN 状态：只在超时后转 HALF_OPEN
            if time.time() - self.opened_at >= self.open_seconds:
                self.state = CircuitState.HALF_OPEN
                self.half_open_successes = 0
                self.half_open_failures = 0
                logger.info("circuit_half_open", endpoint_id=self.endpoint_id)
                # 继续处理本次结果（不 return）
            else:
                return

        if self.state == CircuitState.HALF_OPEN:
            if success:
                self.half_open_successes += 1
                if self.half_open_successes >= self.half_open_probes:
                    self.state = CircuitState.CLOSED
                    logger.info("circuit_closed", endpoint_id=self.endpoint_id)
            else:
                self.half_open_failures += 1
                self.state = CircuitState.OPEN
                self.opened_at = time.time()
                logger.warning("circuit_reopened", endpoint_id=self.endpoint_id)
            return

        # CLOSED 状态：错误率超阈值 → OPEN
        if not success:
            health = self._get_health()
            if health and health.error_rate >= self.error_rate_threshold and health.total_requests >= 5:
                self.state = CircuitState.OPEN
                self.opened_at = time.time()
                logger.warning(
                    "circuit_opened",
                    endpoint_id=self.endpoint_id,
                    error_rate=health.error_rate,
                )

    def is_available(self) -> bool:
        """检查端点是否可接受请求。"""
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if time.time() - self.opened_at >= self.open_seconds:
                self.state = CircuitState.HALF_OPEN
                self.half_open_successes = 0
                self.half_open_failures = 0
                return True
            return False
        return True  # HALF_OPEN：允许探测

    def _get_health(self) -> EndpointHealth | None:
        if self._registry:
            return self._registry.get_health(self.endpoint_id)
        return _health_registry.get_health(self.endpoint_id) if _health_registry else None


class HealthRegistry:
    """端点健康度注册表（进程内，M3 考虑 Redis 共享）。"""

    def __init__(self) -> None:
        self._health: dict[int, EndpointHealth] = {}
        self._breakers: dict[int, CircuitBreaker] = {}
        self._error_rate_threshold: float = 0.5
        self._open_seconds: float = 30.0
        self._half_open_probes: int = 1

    def configure(self, error_rate_threshold: float = 0.5, open_seconds: float = 30.0, half_open_probes: int = 1) -> None:
        self._error_rate_threshold = error_rate_threshold
        self._open_seconds = open_seconds
        self._half_open_probes = half_open_probes

    def get_health(self, endpoint_id: int) -> EndpointHealth:
        if endpoint_id not in self._health:
            self._health[endpoint_id] = EndpointHealth(endpoint_id=endpoint_id)
        return self._health[endpoint_id]

    def get_breaker(self, endpoint_id: int) -> CircuitBreaker:
        if endpoint_id not in self._breakers:
            breaker = CircuitBreaker(
                endpoint_id=endpoint_id,
                error_rate_threshold=self._error_rate_threshold,
                open_seconds=self._open_seconds,
                half_open_probes=self._half_open_probes,
            )
            breaker._registry = self
            self._breakers[endpoint_id] = breaker
        return self._breakers[endpoint_id]

    def record_success(self, endpoint_id: int, latency_ms: float) -> None:
        self.get_health(endpoint_id).record_success(latency_ms)
        self.get_breaker(endpoint_id).record_result(success=True)

    def record_failure(self, endpoint_id: int) -> None:
        self.get_health(endpoint_id).record_failure()
        self.get_breaker(endpoint_id).record_result(success=False)

    def is_available(self, endpoint_id: int) -> bool:
        return self.get_breaker(endpoint_id).is_available()


# 全局健康注册表
_health_registry: HealthRegistry | None = None


def get_health_registry() -> HealthRegistry:
    global _health_registry
    if _health_registry is None:
        _health_registry = HealthRegistry()
    return _health_registry
