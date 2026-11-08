"""API Key 管理 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ...auth.api_key import generate_api_key
from ...auth.audit import audit_log
from ...auth.service import AuthContext
from ...db.repositories.key_repo import KeyRepo
from ...deps import DbSession, require_admin
from ...errors import NotFoundError
from ...models.schemas import ApiKeyCreate, ApiKeyCreatedOut, ApiKeyOut, PaginatedResponse

router = APIRouter(prefix="/admin/v1", tags=["admin-api-keys"])


@router.post("/projects/{project_id}/api-keys", response_model=ApiKeyCreatedOut, status_code=201)
async def create_api_key(
    project_id: int,
    body: ApiKeyCreate,
    request: Request,
    db: DbSession,
    auth: AuthContext = Depends(require_admin),
):
    """生成 API Key（明文仅此一次返回！）。"""
    plaintext, key_hash, key_prefix = generate_api_key()
    repo = KeyRepo(db)

    # 从 project 获取 tenant_id
    from ...db.repositories.tenant_repo import TenantRepo
    project = TenantRepo(db).get_project(project_id)
    if not project:
        raise NotFoundError(message=f"项目不存在: {project_id}")

    key = repo.create_key(
        key_hash=key_hash,
        key_prefix=key_prefix,
        tenant_id=project.tenant_id,
        project_id=project_id,
        name=body.name,
        scopes=body.scopes,
        rpm_limit=body.rpm_limit,
        tpm_limit=body.tpm_limit,
        expires_at=body.expires_at,
    )

    audit_log(
        db,
        actor_type="jwt_user",
        actor_id=str(auth.api_key_id),
        action="api_key.create",
        tenant_id=project.tenant_id,
        resource_type="api_key",
        resource_id=str(key.id),
        ip=request.client.host if request.client else None,
        detail={"key_prefix": key_prefix, "name": body.name},
    )

    return ApiKeyCreatedOut(
        id=key.id,
        key=plaintext,  # 明文仅此一次！
        key_prefix=key_prefix,
        tenant_id=key.tenant_id,
        project_id=key.project_id,
        name=key.name,
        scopes=key.scopes,
        status=key.status,
        rpm_limit=key.rpm_limit,
        tpm_limit=key.tpm_limit,
        expires_at=key.expires_at,
    )


@router.get("/projects/{project_id}/api-keys", response_model=PaginatedResponse)
async def list_api_keys(
    project_id: int,
    db: DbSession,
    page: int = 1,
    page_size: int = 20,
    auth: AuthContext = Depends(require_admin),
):
    """Key 列表（只显 key_prefix，不显明文）。"""
    repo = KeyRepo(db)
    total, items = repo.list_keys(project_id, page=page, page_size=page_size)
    return PaginatedResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[ApiKeyOut.model_validate(k) for k in items],
    )


@router.delete("/api-keys/{key_id}", response_model=ApiKeyOut)
async def revoke_api_key(
    key_id: int,
    request: Request,
    db: DbSession,
    auth: AuthContext = Depends(require_admin),
):
    """吊销 API Key（软删）。"""
    repo = KeyRepo(db)
    key = repo.revoke_key(key_id)
    if not key:
        raise NotFoundError(message=f"Key 不存在: {key_id}")

    audit_log(
        db,
        actor_type="jwt_user",
        actor_id=str(auth.api_key_id),
        action="api_key.revoke",
        tenant_id=key.tenant_id,
        resource_type="api_key",
        resource_id=str(key_id),
        ip=request.client.host if request.client else None,
        detail={"key_prefix": key.key_prefix, "reason": "admin_revoke"},
    )
    return ApiKeyOut.model_validate(key)
