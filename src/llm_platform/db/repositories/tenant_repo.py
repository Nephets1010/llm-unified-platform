"""租户与项目仓储。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models.entities import Project, Tenant


class TenantRepo:
    """租户/项目 CRUD。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ── Tenant ──

    def create_tenant(self, name: str, monthly_budget: float = 1000.0) -> Tenant:
        tenant = Tenant(name=name, monthly_budget=monthly_budget, status=1)
        self.db.add(tenant)
        self.db.commit()
        self.db.refresh(tenant)
        return tenant

    def get_tenant(self, tenant_id: int) -> Tenant | None:
        return self.db.get(Tenant, tenant_id)

    def get_tenant_by_name(self, name: str) -> Tenant | None:
        stmt = select(Tenant).where(Tenant.name == name)
        return self.db.execute(stmt).scalar_one_or_none()

    def list_tenants(self, page: int = 1, page_size: int = 20) -> tuple[int, list[Tenant]]:
        total = len(self.db.execute(select(Tenant)).scalars().all())
        stmt = select(Tenant).offset((page - 1) * page_size).limit(page_size)
        items = list(self.db.execute(stmt).scalars().all())
        return total, items

    def update_tenant(self, tenant_id: int, **kwargs) -> Tenant | None:
        tenant = self.get_tenant(tenant_id)
        if not tenant:
            return None
        for k, v in kwargs.items():
            if v is not None and hasattr(tenant, k):
                setattr(tenant, k, v)
        self.db.commit()
        self.db.refresh(tenant)
        return tenant

    # ── Project ──

    def create_project(
        self, tenant_id: int, name: str, monthly_budget: float | None = None
    ) -> Project:
        project = Project(tenant_id=tenant_id, name=name, monthly_budget=monthly_budget, status=1)
        self.db.add(project)
        self.db.commit()
        self.db.refresh(project)
        return project

    def get_project(self, project_id: int) -> Project | None:
        return self.db.get(Project, project_id)

    def list_projects(self, tenant_id: int, page: int = 1, page_size: int = 20) -> tuple[int, list[Project]]:
        base = select(Project).where(Project.tenant_id == tenant_id)
        total = len(self.db.execute(base).scalars().all())
        stmt = base.offset((page - 1) * page_size).limit(page_size)
        items = list(self.db.execute(stmt).scalars().all())
        return total, items
