"""审计写入：管理操作与调用完成事件，脱敏 detail。"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..db.repositories.audit_repo import AuditRepo
from ..logging import get_logger, get_trace_id
from ..utils.masking import mask_dict

logger = get_logger("audit")


def audit_log(
    db: Session,
    *,
    actor_type: str,
    actor_id: str,
    action: str,
    tenant_id: int | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    ip: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """写入审计日志（脱敏 detail，自动关联 trace_id）。

    Args:
        db: 数据库会话。
        actor_type: api_key / jwt_user / system。
        actor_id: 操作者 ID。
        action: 操作动作（如 tenant.create / key.revoke）。
        tenant_id: 租户 ID（系统级操作可空）。
        resource_type: 资源类型。
        resource_id: 资源 ID。
        ip: 客户端 IP。
        detail: 变更摘要（自动脱敏）。
    """
    safe_detail = mask_dict(detail or {})
    repo = AuditRepo(db)
    try:
        repo.write(
            actor_type=actor_type,
            actor_id=str(actor_id),
            action=action,
            tenant_id=tenant_id,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id else None,
            request_id=get_trace_id(),
            ip=ip,
            detail=safe_detail,
        )
    except Exception:
        logger.warning("audit_write_failed", action=action, actor_id=actor_id)
