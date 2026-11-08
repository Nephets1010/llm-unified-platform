"""月度预算管控：累计/80% 告警/100% 熔断（ADR-003）。"""

from __future__ import annotations

from datetime import UTC, datetime

from ..errors import QuotaExceededError
from ..logging import get_logger

logger = get_logger("ratelimit.budget")


class BudgetGuard:
    """月度预算守卫：Redis 累计 cost，超阈值告警/熔断。

    budget:{tenant_id}:{YYYYMM} = 累计成本（USD）
    """

    def __init__(self, redis_client=None) -> None:
        self.redis = redis_client
        self._local_budgets: dict[str, float] = {}

    def _budget_key(self, tenant_id: int) -> str:
        month = datetime.now(UTC).strftime("%Y%m")
        return f"budget:{tenant_id}:{month}"

    def get_used(self, tenant_id: int) -> float:
        """获取当月已用预算。"""
        key = self._budget_key(tenant_id)
        if self.redis:
            try:
                val = self.redis.get(key)  # type: ignore[union-attr]
                return float(val) if val else 0.0
            except Exception:
                pass
        return self._local_budgets.get(key, 0.0)

    def record_cost(self, tenant_id: int, cost: float) -> None:
        """记录一笔成本。"""
        key = self._budget_key(tenant_id)
        if self.redis:
            try:
                self.redis.incrbyfloat(key, cost)  # type: ignore[union-attr]
                self.redis.expire(key, 86400 * 40)  # type: ignore[union-attr]
                return
            except Exception:
                pass
        self._local_budgets[key] = self._local_budgets.get(key, 0.0) + cost

    def check_budget(self, tenant_id: int, monthly_budget: float, alert_ratio: float = 0.8) -> float:
        """检查预算水位。

        Args:
            tenant_id: 租户 ID。
            monthly_budget: 月度预算总额。
            alert_ratio: 告警阈值比例。

        Returns:
            当前使用比例（0.0~1.0+）。

        Raises:
            QuotaExceededError: 使用比例 ≥ 100%。
        """
        used = self.get_used(tenant_id)
        ratio = used / monthly_budget if monthly_budget > 0 else 0.0

        if ratio >= 1.0:
            logger.warning(
                "budget_exceeded",
                tenant_id=tenant_id,
                used=used,
                budget=monthly_budget,
                ratio=ratio,
            )
            raise QuotaExceededError(
                message=f"月度预算已耗尽（{used:.4f}/{monthly_budget:.2f}）",
                tenant_id=tenant_id,
                used=round(used, 4),
                budget=monthly_budget,
                ratio=round(ratio, 4),
            )

        if ratio >= alert_ratio:
            logger.warning(
                "budget_alert_80",
                tenant_id=tenant_id,
                used=used,
                budget=monthly_budget,
                ratio=ratio,
            )

        return ratio

    def reset_month(self, tenant_id: int) -> None:
        """月初重置（由 worker 调用，或 key 自然过期）。"""
        key = self._budget_key(tenant_id)
        if self.redis:
            try:
                self.redis.delete(key)  # type: ignore[union-attr]
                return
            except Exception:
                pass
        self._local_budgets.pop(key, None)
