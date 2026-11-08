"""调用查询 API（强制时间范围，命中分区裁剪）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query

from ...auth.service import AuthContext
from ...db.repositories.call_log_repo import CallLogRepo
from ...deps import DbSession, require_admin
from ...errors import NotFoundError
from ...models.schemas import CallLogItem, PaginatedResponse

router = APIRouter(prefix="/admin/v1/calls", tags=["admin-calls"])


@router.get("", response_model=PaginatedResponse)
async def list_calls(
    db: DbSession,
    tenant_id: int | None = None,
    project_id: int | None = None,
    model: str | None = None,
    status: str | None = None,
    source: str | None = None,
    trace_id: str | None = None,
    created_from: datetime | None = Query(default=None),
    created_to: datetime | None = Query(default=None),
    page: int = 1,
    page_size: int = 20,
    auth: AuthContext = Depends(require_admin),
):
    """调用查询（强制 JWT 租户过滤 + 时间范围 → 分区裁剪）。

    时间范围默认近 7 天。
    """
    # 时间范围默认值（分区裁剪必须）
    now = datetime.now(UTC)
    if created_from is None:
        created_from = now - timedelta(days=7)
    if created_to is None:
        created_to = now

    # 非 admin 只能看自己租户
    effective_tenant_id = tenant_id
    if auth.role != "admin":
        effective_tenant_id = auth.tenant_id

    repo = CallLogRepo(db)
    total, items = repo.query_calls(
        tenant_id=effective_tenant_id,
        project_id=project_id,
        model=model,
        status=status,
        source=source,
        trace_id=trace_id,
        created_from=created_from,
        created_to=created_to,
        page=page,
        page_size=page_size,
    )
    return PaginatedResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[CallLogItem.model_validate(c) for c in items],
    )


@router.get("/{trace_id}")
async def get_call_detail(
    trace_id: str,
    db: DbSession,
    auth: AuthContext = Depends(require_admin),
):
    """单条全链路详情（含 attempt 明细）。"""
    repo = CallLogRepo(db)
    items = repo.get_by_trace_id(trace_id)
    if not items:
        raise NotFoundError(message=f"trace_id 不存在: {trace_id}")

    return {
        "trace_id": trace_id,
        "attempts": len(items),
        "items": [CallLogItem.model_validate(c) for c in items],
    }
