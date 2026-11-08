"""审计日志仓储。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models.entities import AuditLog


class AuditRepo:
    """audit_log 写入与查询。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    def write(
        self,
        *,
        actor_type: str,
        actor_id: str,
        action: str,
        tenant_id: int | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        request_id: str | None = None,
        ip: str | None = None,
        detail: dict | None = None,
    ) -> AuditLog:
        log = AuditLog(
            tenant_id=tenant_id,
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            request_id=request_id,
            ip=ip,
            detail=detail or {},
        )
        self.db.add(log)
        self.db.commit()
        self.db.refresh(log)
        return log

    def query(
        self,
        *,
        request_id: str | None = None,
        tenant_id: int | None = None,
        actor_id: str | None = None,
        action: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, list[AuditLog]]:
        stmt = select(AuditLog)
        if request_id:
            stmt = stmt.where(AuditLog.request_id == request_id)
        if tenant_id is not None:
            stmt = stmt.where(AuditLog.tenant_id == tenant_id)
        if actor_id:
            stmt = stmt.where(AuditLog.actor_id == actor_id)
        if action:
            stmt = stmt.where(AuditLog.action == action)
        if created_from:
            stmt = stmt.where(AuditLog.created_at >= created_from)
        if created_to:
            stmt = stmt.where(AuditLog.created_at <= created_to)

        total = len(self.db.execute(stmt).scalars().all())
        stmt = stmt.order_by(AuditLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        items = list(self.db.execute(stmt).scalars().all())
        return total, items
