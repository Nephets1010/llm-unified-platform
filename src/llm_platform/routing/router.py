"""路由器：选路 + failover 候选排序 + 灰度权重（ADR-002）。

failover P95 ≤200ms：候选预排序 + 单次快速重试。
M3 增强：canary_weight 灰度流量分配（0~1，控制新版本端点流量占比）。
GAP-3-3：支持租户/Key 白名单粒度灰度。
"""

from __future__ import annotations

import random

from ..config import get_settings
from ..errors import NoRouteError, UpstreamError
from ..logging import get_logger
from ..models.entities import ModelEndpoint
from .health import get_health_registry
from .strategies import RouteStrategy, get_strategy

logger = get_logger("routing.router")


class Router:
    """模型路由器：从模型池选路并管理 failover 顺序。"""

    def __init__(self, strategy: RouteStrategy | None = None) -> None:
        settings = get_settings()
        self.strategy = strategy or get_strategy(settings.routing_strategy)
        self.failover_max = settings.routing_failover_max
        # 灰度权重：0~1，控制 canary 端点流量占比
        self.canary_weight: float = 0.0
        self._canary_endpoints: set[int] = set()
        # GAP-3-3：租户/Key 白名单粒度（空 = 全量灰度，非空 = 仅命中白名单的请求参与灰度）
        self._canary_tenant_ids: set[int] = set()
        self._canary_key_hashes: set[str] = set()

    def set_strategy(self, name: str) -> None:
        """热更新路由策略（配置变更时调用）。"""
        self.strategy = get_strategy(name)
        logger.info("routing_strategy_changed", strategy=name)

    def set_canary(
        self,
        endpoint_ids: list[int],
        weight: float,
        tenant_ids: list[int] | None = None,
        key_hashes: list[str] | None = None,
    ) -> None:
        """设置灰度端点及流量权重（T12 / GAP-3-3）。

        Args:
            endpoint_ids: 灰度端点 ID 列表。
            weight: 灰度流量占比 0~1。
            tenant_ids: 租户白名单（空/None = 全量灰度）。
            key_hashes: API Key 哈希白名单（空/None = 全量灰度）。
        """
        self._canary_endpoints = set(endpoint_ids)
        self.canary_weight = max(0.0, min(1.0, weight))
        self._canary_tenant_ids = set(tenant_ids or [])
        self._canary_key_hashes = set(key_hashes or [])
        logger.info(
            "canary_configured",
            endpoint_ids=endpoint_ids,
            weight=self.canary_weight,
            tenant_scope=len(self._canary_tenant_ids),
            key_scope=len(self._canary_key_hashes),
        )

    def clear_canary(self) -> None:
        """清除灰度配置（回滚时调用）。"""
        self._canary_endpoints.clear()
        self.canary_weight = 0.0
        self._canary_tenant_ids.clear()
        self._canary_key_hashes.clear()
        logger.info("canary_cleared")

    def _in_canary_scope(self, tenant_id: int | None = None, key_hash: str | None = None) -> bool:
        """判断请求是否命中灰度白名单（GAP-3-3）。

        白名单为空时视为全量灰度（兼容 M3 端点级灰度）。
        命中任一维度即可参与灰度分流。
        """
        has_scope = bool(self._canary_tenant_ids) or bool(self._canary_key_hashes)
        if not has_scope:
            return True  # 无白名单限制 = 全量灰度
        if tenant_id is not None and tenant_id in self._canary_tenant_ids:
            return True
        if key_hash is not None and key_hash in self._canary_key_hashes:
            return True
        return False

    def _should_route_canary(
        self, tenant_id: int | None = None, key_hash: str | None = None
    ) -> bool:
        """按权重+白名单决定本次请求是否走灰度端点。"""
        if self.canary_weight <= 0:
            return False
        if not self._in_canary_scope(tenant_id, key_hash):
            return False
        return random.random() < self.canary_weight

    def select_endpoint(
        self,
        candidates: list[ModelEndpoint],
        tenant_id: int | None = None,
        key_hash: str | None = None,
    ) -> ModelEndpoint:
        """选路：返回最佳可用端点（支持灰度权重+租户/Key 白名单分流）。

        Args:
            candidates: 候选端点列表。
            tenant_id: 租户 ID（GAP-3-3 白名单匹配）。
            key_hash: API Key 哈希（GAP-3-3 白名单匹配）。

        Raises:
            NoRouteError: 模型池无健康端点。
        """
        if not candidates:
            raise NoRouteError(message="模型池为空", model_pool="empty")

        registry = get_health_registry()
        available = [e for e in candidates if registry.is_available(e.id)]
        if not available:
            raise NoRouteError(message="所有端点均不可用", endpoint_count=len(candidates))

        # 灰度分流：按 canary_weight + 白名单概率选择灰度端点
        if self._canary_endpoints and self._should_route_canary(tenant_id, key_hash):
            canary_available = [e for e in available if e.id in self._canary_endpoints]
            if canary_available:
                selected = self.strategy.select(canary_available)
                if selected:
                    logger.debug("canary_routed", endpoint_id=selected.id)
                    return selected

        # 正常选路（排除灰度端点，避免灰度流量混入主流量）
        main_available = [e for e in available if e.id not in self._canary_endpoints]
        if not main_available:
            main_available = available  # 全是灰度端点时兜底

        selected = self.strategy.select(main_available)
        if selected is None:
            raise NoRouteError(message="策略未能选出端点", strategy=self.strategy.name)
        return selected

    def order_candidates(self, candidates: list[ModelEndpoint]) -> list[ModelEndpoint]:
        """failover 候选排序：优先级升序，同优先级按策略打分。"""
        registry = get_health_registry()
        available = [e for e in candidates if registry.is_available(e.id)]
        unavailable = [e for e in candidates if not registry.is_available(e.id)]

        # 按 priority 升序，同 priority 内按策略选路打分排序
        def sort_key(e: ModelEndpoint) -> tuple[int, float]:
            h = registry.get_health(e.id)
            # 负分 = 越好排越前（Python sorted 升序）
            score = -(0.7 * (1.0 - h.error_rate) + 0.3 / (1.0 + h.avg_latency / 1000.0))
            return (e.priority, score)

        ordered = sorted(available, key=sort_key)
        ordered.extend(sorted(unavailable, key=lambda e: e.priority))
        return ordered

    async def execute_with_failover(
        self,
        candidates: list[ModelEndpoint],
        call_fn,  # noqa: ANN001
    ) -> tuple[ModelEndpoint, object]:
        """带 failover 的调用执行。

        Args:
            candidates: 候选端点列表。
            call_fn: 异步调用函数 (endpoint) -> result。

        Returns:
            (endpoint, result)

        Raises:
            UpstreamError: 所有候选均失败。
            NoRouteError: 无可用候选。
        """
        ordered = self.order_candidates(candidates)
        available = [e for e in ordered if get_health_registry().is_available(e.id)]
        if not available:
            raise NoRouteError(message="无可用端点")

        max_attempts = min(self.failover_max + 1, len(available))
        last_error: Exception | None = None

        for attempt, endpoint in enumerate(available[:max_attempts]):
            try:
                result = await call_fn(endpoint)
                get_health_registry().record_success(endpoint.id, 0)
                return endpoint, result
            except (UpstreamError, TimeoutError, ConnectionError, OSError) as exc:
                last_error = exc
                get_health_registry().record_failure(endpoint.id)
                logger.warning(
                    "endpoint_failover",
                    endpoint_id=endpoint.id,
                    provider=endpoint.provider,
                    attempt=attempt + 1,
                    error=str(exc),
                )

        raise UpstreamError(
            message=f"所有端点均失败（尝试 {max_attempts} 次）",
            last_error=str(last_error) if last_error else None,
        )
