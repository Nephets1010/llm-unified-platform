"""租户与项目管理 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ...auth.audit import audit_log
from ...auth.service import AuthContext
from ...db.repositories.tenant_repo import TenantRepo
from ...deps import DbSession, require_admin
from ...errors import ConflictError, NotFoundError
from ...models.schemas import (
    PaginatedResponse,
    ProjectCreate,
    ProjectOut,
    TenantCreate,
    TenantOut,
    TenantUpdate,
)

router = APIRouter(prefix="/admin/v1", tags=["admin-tenants"])


@router.post("/tenants", response_model=TenantOut, status_code=201)
async def create_tenant(
    body: TenantCreate,
    request: Request,
    db: DbSession,
    auth: AuthContext = Depends(require_admin),
):
    """创建租户。"""
    repo = TenantRepo(db)
    existing = repo.get_tenant_by_name(body.name)
    if existing:
        raise ConflictError(message=f"租户名已存在: {body.name}")

    tenant = repo.create_tenant(name=body.name, monthly_budget=body.monthly_budget)
    audit_log(
        db,
        actor_type="jwt_user",
        actor_id=str(auth.api_key_id),
        action="tenant.create",
        tenant_id=tenant.id,
        resource_type="tenant",
        resource_id=str(tenant.id),
        ip=request.client.host if request.client else None,
        detail={"name": body.name, "monthly_budget": body.monthly_budget},
    )
    return TenantOut.model_validate(tenant)


@router.get("/tenants", response_model=PaginatedResponse)
async def list_tenants(
    db: DbSession,
    page: int = 1,
    page_size: int = 20,
    auth: AuthContext = Depends(require_admin),
):
    """租户列表（分页）。"""
    repo = TenantRepo(db)
    total, items = repo.list_tenants(page=page, page_size=page_size)
    return PaginatedResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[TenantOut.model_validate(t) for t in items],
    )


@router.get("/tenants/{tenant_id}", response_model=TenantOut)
async def get_tenant(
    tenant_id: int,
    db: DbSession,
    auth: AuthContext = Depends(require_admin),
):
    """租户详情（含预算水位）。"""
    repo = TenantRepo(db)
    tenant = repo.get_tenant(tenant_id)
    if not tenant:
        raise NotFoundError(message=f"租户不存在: {tenant_id}")
    return TenantOut.model_validate(tenant)


@router.patch("/tenants/{tenant_id}", response_model=TenantOut)
async def update_tenant(
    tenant_id: int,
    body: TenantUpdate,
    request: Request,
    db: DbSession,
    auth: AuthContext = Depends(require_admin),
):
    """修改预算/状态（热更新 ≤5s）。"""
    repo = TenantRepo(db)
    tenant = repo.update_tenant(
        tenant_id,
        monthly_budget=body.monthly_budget,
        status=body.status,
    )
    if not tenant:
        raise NotFoundError(message=f"租户不存在: {tenant_id}")

    audit_log(
        db,
        actor_type="jwt_user",
        actor_id=str(auth.api_key_id),
        action="tenant.update",
        tenant_id=tenant_id,
        resource_type="tenant",
        resource_id=str(tenant_id),
        ip=request.client.host if request.client else None,
        detail={"monthly_budget": body.monthly_budget, "status": body.status},
    )
    return TenantOut.model_validate(tenant)


@router.post("/tenants/{tenant_id}/projects", response_model=ProjectOut, status_code=201)
async def create_project(
    tenant_id: int,
    body: ProjectCreate,
    request: Request,
    db: DbSession,
    auth: AuthContext = Depends(require_admin),
):
    """创建项目。"""
    repo = TenantRepo(db)
    tenant = repo.get_tenant(tenant_id)
    if not tenant:
        raise NotFoundError(message=f"租户不存在: {tenant_id}")

    project = repo.create_project(
        tenant_id=tenant_id, name=body.name, monthly_budget=body.monthly_budget
    )
    audit_log(
        db,
        actor_type="jwt_user",
        actor_id=str(auth.api_key_id),
        action="project.create",
        tenant_id=tenant_id,
        resource_type="project",
        resource_id=str(project.id),
        ip=request.client.host if request.client else None,
        detail={"name": body.name},
    )
    return ProjectOut.model_validate(project)


@router.get("/tenants/{tenant_id}/projects", response_model=PaginatedResponse)
async def list_projects(
    tenant_id: int,
    db: DbSession,
    page: int = 1,
    page_size: int = 20,
    auth: AuthContext = Depends(require_admin),
):
    """项目列表。"""
    repo = TenantRepo(db)
    total, items = repo.list_projects(tenant_id, page=page, page_size=page_size)
    return PaginatedResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[ProjectOut.model_validate(p) for p in items],
    )
