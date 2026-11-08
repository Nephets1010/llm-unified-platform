"""账单查询 API（P1-08）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from ...auth.service import AuthContext
from ...deps import DbSession, require_admin
from ...monitoring.billing import BillingService

router = APIRouter(prefix="/admin/v1/billing", tags=["admin-billing"])


@router.get("/summary")
async def get_billing_summary(
    db: DbSession,
    tenant_id: int | None = None,
    project_id: int | None = None,
    month: str | None = None,
    auth: AuthContext = Depends(require_admin),
):
    """账单汇总。"""
    service = BillingService(db)
    return service.summary(tenant_id=tenant_id, project_id=project_id, month=month)


@router.get("/export")
async def export_billing(
    db: DbSession,
    format: str = "json",  # noqa: A002
    tenant_id: int | None = None,
    month: str | None = None,
    auth: AuthContext = Depends(require_admin),
):
    """导出 CSV/JSON（自核算口径，不接外部计费）。"""
    service = BillingService(db)
    if format == "csv":
        content = service.export_csv(tenant_id=tenant_id, month=month)
        return PlainTextResponse(content, media_type="text/csv")
    else:
        return service.export_json(tenant_id=tenant_id, month=month)
