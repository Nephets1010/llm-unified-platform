"""调用日志仓储：支持分区裁剪查询（强制时间范围）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...models.entities import CallLog


class CallLogRepo:
    """call_log CRUD 与时间范围查询（命中分区裁剪）。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    def insert_many(self, records: list[dict]) -> int:
        """批量插入 call_log 记录（executemany 风格）。"""
        if not records:
            return 0
        objs = [CallLog(**r) for r in records]
        self.db.add_all(objs)
        self.db.commit()
        return len(objs)

    def query_calls(
        self,
        *,
        tenant_id: int | None = None,
        project_id: int | None = None,
        model: str | None = None,
        status: str | None = None,
        source: str | None = None,
        trace_id: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, list[CallLog]]:
        """按条件查询 call_log。时间范围必带（分区裁剪），默认近 7 天。"""
        stmt = select(CallLog)
        if tenant_id is not None:
            stmt = stmt.where(CallLog.tenant_id == tenant_id)
        if project_id is not None:
            stmt = stmt.where(CallLog.project_id == project_id)
        if model:
            stmt = stmt.where(CallLog.model == model)
        if status:
            stmt = stmt.where(CallLog.status == status)
        if source:
            stmt = stmt.where(CallLog.source == source)
        if trace_id:
            stmt = stmt.where(CallLog.trace_id == trace_id)
        if created_from:
            stmt = stmt.where(CallLog.created_at >= created_from)
        if created_to:
            stmt = stmt.where(CallLog.created_at <= created_to)

        total = len(self.db.execute(stmt).scalars().all())
        stmt = stmt.order_by(CallLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        items = list(self.db.execute(stmt).scalars().all())
        return total, items

    def get_by_trace_id(self, trace_id: str) -> list[CallLog]:
        """按 trace_id 查全链路调用记录。"""
        stmt = select(CallLog).where(CallLog.trace_id == trace_id).order_by(CallLog.created_at)
        return list(self.db.execute(stmt).scalars().all())

    def sum_cost_by_tenant(self, tenant_id: int, month_prefix: str) -> float:
        """按月统计租户成本（month_prefix 如 "2026-01"）。"""
        stmt = select(func.coalesce(func.sum(CallLog.cost), 0.0)).where(
            CallLog.tenant_id == tenant_id,
            func.strftime("%Y-%m", CallLog.created_at) == month_prefix
            if self.db.bind.dialect.name == "sqlite"
            else func.to_char(CallLog.created_at, "YYYY-MM") == month_prefix,
        )
        return float(self.db.execute(stmt).scalar() or 0.0)
