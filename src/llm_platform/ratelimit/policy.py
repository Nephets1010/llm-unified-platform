"""限流策略解析：rate_limit_policy 表读取与默认值。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models.entities import RateLimitPolicy


class RateLimitPolicyResolver:
    """限流策略解析器：从 DB 读取策略，未配置时用默认值。"""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()

    def resolve(self, subject_type: str, subject_id: str) -> tuple[int, int]:
        """解析主体的 RPM/TPM 限制。

        优先级：rate_limit_policy 表 > 默认配置值。

        Returns:
            (rpm_limit, tpm_limit)
        """
        stmt = select(RateLimitPolicy).where(
            RateLimitPolicy.subject_type == subject_type,
            RateLimitPolicy.subject_id == str(subject_id),
            RateLimitPolicy.enabled == True,  # noqa: E712
        )
        policy = self.db.execute(stmt).scalar_one_or_none()

        if policy:
            return policy.rpm_limit, policy.tpm_limit

        return self.settings.rate_limit_default_rpm, self.settings.rate_limit_default_tpm

    def set_policy(
        self,
        subject_type: str,
        subject_id: str,
        rpm_limit: int,
        tpm_limit: int,
    ) -> RateLimitPolicy:
        """设置/更新限流策略。"""
        stmt = select(RateLimitPolicy).where(
            RateLimitPolicy.subject_type == subject_type,
            RateLimitPolicy.subject_id == str(subject_id),
        )
        policy = self.db.execute(stmt).scalar_one_or_none()

        if policy:
            policy.rpm_limit = rpm_limit
            policy.tpm_limit = tpm_limit
            policy.enabled = True
        else:
            policy = RateLimitPolicy(
                subject_type=subject_type,
                subject_id=str(subject_id),
                rpm_limit=rpm_limit,
                tpm_limit=tpm_limit,
                enabled=True,
            )
            self.db.add(policy)

        self.db.commit()
        self.db.refresh(policy)
        return policy
