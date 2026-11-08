"""模型端点管理 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ...auth.audit import audit_log
from ...auth.service import AuthContext
from ...db.repositories.endpoint_repo import EndpointRepo
from ...deps import DbSession, require_admin
from ...errors import NotFoundError
from ...models.schemas import EndpointCreate, EndpointOut, EndpointUpdate, PaginatedResponse

router = APIRouter(prefix="/admin/v1/endpoints", tags=["admin-endpoints"])


@router.post("", response_model=EndpointOut, status_code=201)
async def create_endpoint(
    body: EndpointCreate,
    request: Request,
    db: DbSession,
    auth: AuthContext = Depends(require_admin),
):
    """创建模型端点。credential_env 只是环境变量名，不接受/返回密钥本体。"""
    repo = EndpointRepo(db)
    endpoint = repo.create_endpoint(
        provider=body.provider,
        protocol=body.protocol,
        model_name=body.model_name,
        base_url=body.base_url,
        credential_env=body.credential_env,
        weight=body.weight,
        priority=body.priority,
        canary_group=body.canary_group,
        cost_per_1k_prompt=body.cost_per_1k_prompt,
        cost_per_1k_completion=body.cost_per_1k_completion,
    )
    audit_log(
        db,
        actor_type="jwt_user",
        actor_id=str(auth.api_key_id),
        action="endpoint.create",
        resource_type="model_endpoint",
        resource_id=str(endpoint.id),
        ip=request.client.host if request.client else None,
        detail={"provider": body.provider, "model_name": body.model_name},
    )
    return EndpointOut.model_validate(endpoint)


@router.get("", response_model=PaginatedResponse)
async def list_endpoints(
    db: DbSession,
    page: int = 1,
    page_size: int = 50,
    auth: AuthContext = Depends(require_admin),
):
    """端点列表。"""
    repo = EndpointRepo(db)
    total, items = repo.list_endpoints(page=page, page_size=page_size)
    return PaginatedResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[EndpointOut.model_validate(e) for e in items],
    )


@router.patch("/{endpoint_id}", response_model=EndpointOut)
async def update_endpoint(
    endpoint_id: int,
    body: EndpointUpdate,
    request: Request,
    db: DbSession,
    auth: AuthContext = Depends(require_admin),
):
    """修改端点配置。"""
    repo = EndpointRepo(db)
    endpoint = repo.update_endpoint(
        endpoint_id,
        enabled=body.enabled,
        weight=body.weight,
        priority=body.priority,
        canary_group=body.canary_group,
        cost_per_1k_prompt=body.cost_per_1k_prompt,
        cost_per_1k_completion=body.cost_per_1k_completion,
        healthy=body.healthy,
    )
    if not endpoint:
        raise NotFoundError(message=f"端点不存在: {endpoint_id}")

    audit_log(
        db,
        actor_type="jwt_user",
        actor_id=str(auth.api_key_id),
        action="endpoint.update",
        resource_type="model_endpoint",
        resource_id=str(endpoint_id),
        ip=request.client.host if request.client else None,
        detail={"enabled": body.enabled, "weight": body.weight},
    )
    return EndpointOut.model_validate(endpoint)


@router.delete("/{endpoint_id}")
async def delete_endpoint(
    endpoint_id: int,
    request: Request,
    db: DbSession,
    auth: AuthContext = Depends(require_admin),
):
    """删除端点。"""
    repo = EndpointRepo(db)
    ok = repo.delete_endpoint(endpoint_id)
    if not ok:
        raise NotFoundError(message=f"端点不存在: {endpoint_id}")

    audit_log(
        db,
        actor_type="jwt_user",
        actor_id=str(auth.api_key_id),
        action="endpoint.delete",
        resource_type="model_endpoint",
        resource_id=str(endpoint_id),
        ip=request.client.host if request.client else None,
    )
    return {"deleted": True, "id": endpoint_id}
