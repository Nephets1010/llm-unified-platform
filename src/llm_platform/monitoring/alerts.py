"""告警规则引擎：5xx/P95/预算 80% 写 risk_alert（P1-08）。"""

from __future__ import annotations

from datetime import UTC
from typing import Any

from sqlalchemy.orm import Session

from ..logging import get_logger
from ..models.entities import RiskAlert

logger = get_logger("monitoring.alerts")


class AlertEngine:
    """告警规则引擎。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    def fire(
        self,
        level: str,
        rule_code: str,
        subject_type: str,
        subject_id: str,
        message: str,
        score: float = 0.0,
        evidence: dict[str, Any] | None = None,
    ) -> RiskAlert:
        """触发告警，写 risk_alert 表。"""
        alert = RiskAlert(
            level=level,
            rule_code=rule_code,
            subject_type=subject_type,
            subject_id=str(subject_id),
            message=message,
            score=score,
            evidence=evidence or {},
            resolved=False,
        )
        self.db.add(alert)
        self.db.commit()
        self.db.refresh(alert)
        logger.warning(
            "alert_fired",
            level=level,
            rule_code=rule_code,
            subject_id=subject_id,
            message=message,
        )
        return alert

    def check_budget_80(self, tenant_id: int, used_ratio: float, used: float, budget: float) -> RiskAlert | None:
        """预算 80% 告警规则。"""
        if used_ratio >= 0.8:
            return self.fire(
                level="orange",
                rule_code="BUDGET_80",
                subject_type="tenant",
                subject_id=str(tenant_id),
                message=f"租户预算已达 {used_ratio:.0%}",
                score=round(used_ratio, 4),
                evidence={"budget_used": used, "budget": budget},
            )
        return None

    def check_error_rate(self, subject_type: str, subject_id: str, error_rate: float, threshold: float = 0.05) -> RiskAlert | None:
        """错误率告警规则。"""
        if error_rate >= threshold:
            return self.fire(
                level="red",
                rule_code="ERROR_RATE",
                subject_type=subject_type,
                subject_id=subject_id,
                message=f"错误率 {error_rate:.1%} 超阈值 {threshold:.1%}",
                score=round(error_rate, 4),
                evidence={"error_rate": error_rate, "threshold": threshold},
            )
        return None

    def resolve(self, alert_id: int) -> RiskAlert | None:
        """标记告警已处理。"""
        from datetime import datetime

        alert = self.db.get(RiskAlert, alert_id)
        if alert:
            alert.resolved = True
            alert.resolved_at = datetime.now(UTC)
            self.db.commit()
            self.db.refresh(alert)
        return alert

    def list_alerts(
        self,
        resolved: bool | None = None,
        level: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, list[RiskAlert]]:
        """查询告警列表。"""
        from sqlalchemy import select

        stmt = select(RiskAlert)
        if resolved is not None:
            stmt = stmt.where(RiskAlert.resolved == resolved)
        if level:
            stmt = stmt.where(RiskAlert.level == level)

        total = len(self.db.execute(stmt).scalars().all())
        stmt = stmt.order_by(RiskAlert.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        items = list(self.db.execute(stmt).scalars().all())
        return total, items
