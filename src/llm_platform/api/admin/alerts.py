"""告警管理 API（P1-08）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ...auth.service import AuthContext
from ...deps import DbSession, require_admin
from ...errors import NotFoundError
from ...monitoring.alerts import AlertEngine

router = APIRouter(prefix="/admin/v1/alerts", tags=["admin-alerts"])


@router.get("")
async def list_alerts(
    db: DbSession,
    resolved: bool | None = None,
    level: str | None = None,
    page: int = 1,
    page_size: int = 20,
    auth: AuthContext = Depends(require_admin),
):
    """告警列表。"""
    engine = AlertEngine(db)
    total, items = engine.list_alerts(resolved=resolved, level=level, page=page, page_size=page_size)
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": a.id,
                "level": a.level,
                "rule_code": a.rule_code,
                "subject_type": a.subject_type,
                "subject_id": a.subject_id,
                "message": a.message,
                "score": a.score,
                "evidence": a.evidence,
                "resolved": a.resolved,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in items
        ],
    }


@router.patch("/{alert_id}/resolve")
async def resolve_alert(
    alert_id: int,
    db: DbSession,
    auth: AuthContext = Depends(require_admin),
):
    """标记告警已处理。"""
    engine = AlertEngine(db)
    alert = engine.resolve(alert_id)
    if not alert:
        raise NotFoundError(message=f"告警不存在: {alert_id}")
    return {"id": alert_id, "resolved": True}
