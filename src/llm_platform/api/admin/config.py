"""配置管理 API（P1-09）：配置/灰度/回滚（ADR-008）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ...auth.audit import audit_log
from ...auth.service import AuthContext
from ...config_center.store import ConfigStore
from ...deps import DbSession, RedisClient, require_admin
from ...logging import get_logger

logger = get_logger("api.admin.config")
router = APIRouter(prefix="/admin/v1/config", tags=["admin-config"])


class ConfigPayload(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)


@router.get("/{namespace}")
async def get_config(
    namespace: str,
    db: DbSession,
    redis: RedisClient,
    auth: AuthContext = Depends(require_admin),
):
    """获取指定命名空间的最新配置。"""
    store = ConfigStore(db, redis_client=redis)
    current = store.get_current(namespace)
    if current is None:
        return {"namespace": namespace, "version": 0, "payload": {}}
    revisions = store.list_revisions(namespace)
    latest = revisions[0] if revisions else None
    return {
        "namespace": namespace,
        "version": latest.version if latest else 0,
        "payload": current,
        "status": latest.status if latest else "none",
        "applied_at": latest.applied_at.isoformat() if latest and latest.applied_at else None,
    }


@router.put("/{namespace}")
async def put_config(
    namespace: str,
    body: ConfigPayload,
    request: Request,
    db: DbSession,
    redis: RedisClient,
    auth: AuthContext = Depends(require_admin),
):
    """全量快照提交，version+1，≤5s 生效（pub/sub 广播）。"""
    store = ConfigStore(db, redis_client=redis)
    rev = store.put(namespace, body.payload, created_by=str(auth.api_key_id))

    audit_log(
        db,
        actor_type="jwt_user",
        actor_id=str(auth.api_key_id),
        action="config.update",
        resource_type="config_revision",
        resource_id=str(rev.id),
        ip=request.client.host if request.client else None,
        detail={"namespace": namespace, "version": rev.version},
    )

    return {
        "namespace": namespace,
        "version": rev.version,
        "status": "applied",
        "applied_at": rev.applied_at.isoformat() if rev.applied_at else None,
        "effective_within_seconds": 5,
    }


@router.get("/{namespace}/revisions")
async def list_revisions(
    namespace: str,
    db: DbSession,
    redis: RedisClient,
    auth: AuthContext = Depends(require_admin),
):
    """变更历史。"""
    store = ConfigStore(db, redis_client=redis)
    revisions = store.list_revisions(namespace)
    return {
        "namespace": namespace,
        "items": [
            {
                "id": r.id,
                "version": r.version,
                "status": r.status,
                "created_by": r.created_by,
                "applied_at": r.applied_at.isoformat() if r.applied_at else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in revisions
        ],
    }


@router.post("/{namespace}/rollback")
async def rollback_config(
    namespace: str,
    request: Request,
    db: DbSession,
    redis: RedisClient,
    auth: AuthContext = Depends(require_admin),
):
    """回滚至上一版本（≤1 分钟）。"""
    store = ConfigStore(db, redis_client=redis)
    rev = store.rollback(namespace, created_by=str(auth.api_key_id))

    audit_log(
        db,
        actor_type="jwt_user",
        actor_id=str(auth.api_key_id),
        action="config.rollback",
        resource_type="config_revision",
        resource_id=str(rev.id),
        ip=request.client.host if request.client else None,
        detail={"namespace": namespace, "version": rev.version},
    )

    return {
        "namespace": namespace,
        "version": rev.version,
        "status": "applied",
        "applied_at": rev.applied_at.isoformat() if rev.applied_at else None,
        "effective_within_seconds": 5,
    }
