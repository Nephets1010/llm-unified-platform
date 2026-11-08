"""模型端点仓储。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models.entities import ModelEndpoint


class EndpointRepo:
    """模型端点 CRUD 与模型池查询。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    def create_endpoint(self, **kwargs) -> ModelEndpoint:
        endpoint = ModelEndpoint(**kwargs)
        self.db.add(endpoint)
        self.db.commit()
        self.db.refresh(endpoint)
        return endpoint

    def get_endpoint(self, endpoint_id: int) -> ModelEndpoint | None:
        return self.db.get(ModelEndpoint, endpoint_id)

    def list_endpoints(
        self, enabled_only: bool = False, page: int = 1, page_size: int = 50
    ) -> tuple[int, list[ModelEndpoint]]:
        base = select(ModelEndpoint)
        if enabled_only:
            base = base.where(ModelEndpoint.enabled == True)  # noqa: E712
        total = len(self.db.execute(base).scalars().all())
        stmt = base.offset((page - 1) * page_size).limit(page_size)
        items = list(self.db.execute(stmt).scalars().all())
        return total, items

    def list_by_model(self, model_name: str) -> list[ModelEndpoint]:
        """获取指定逻辑模型名的所有启用端点（路由候选池）。"""
        stmt = (
            select(ModelEndpoint)
            .where(ModelEndpoint.model_name == model_name)
            .where(ModelEndpoint.enabled == True)  # noqa: E712
        )
        return list(self.db.execute(stmt).scalars().all())

    def list_model_names(self) -> list[str]:
        """返回所有启用的去重逻辑模型名（/v1/models 用）。"""
        stmt = select(ModelEndpoint.model_name).where(ModelEndpoint.enabled == True).distinct()  # noqa: E712
        return list(self.db.execute(stmt).scalars().all())

    def update_endpoint(self, endpoint_id: int, **kwargs) -> ModelEndpoint | None:
        endpoint = self.get_endpoint(endpoint_id)
        if not endpoint:
            return None
        for k, v in kwargs.items():
            if v is not None and hasattr(endpoint, k):
                setattr(endpoint, k, v)
        self.db.commit()
        self.db.refresh(endpoint)
        return endpoint

    def delete_endpoint(self, endpoint_id: int) -> bool:
        endpoint = self.get_endpoint(endpoint_id)
        if not endpoint:
            return False
        self.db.delete(endpoint)
        self.db.commit()
        return True

    def update_latency(self, endpoint_id: int, latency_ms: float) -> None:
        """EWMA 更新平均延迟。"""
        endpoint = self.get_endpoint(endpoint_id)
        if endpoint:
            alpha = 0.3
            endpoint.avg_latency_ms = alpha * latency_ms + (1 - alpha) * endpoint.avg_latency_ms
            self.db.commit()
