"""GET /v1/models — 返回可用逻辑模型列表。"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends

from ...auth.service import AuthContext
from ...db.repositories.endpoint_repo import EndpointRepo
from ...deps import DbSession, get_current_auth
from ...models.schemas import ModelInfo, ModelListResponse

router = APIRouter(prefix="/v1", tags=["models"])


@router.get("/models", response_model=ModelListResponse)
async def list_models(
    db: DbSession,
    auth: AuthContext = Depends(get_current_auth),
):
    """返回 model_endpoint 中 enabled=true 的逻辑模型去重列表。"""
    repo = EndpointRepo(db)
    model_names = repo.list_model_names()

    data = [
        ModelInfo(
            id=name,
            created=int(time.time()),
            owned_by=name.split("-")[0] if "-" in name else name,
        )
        for name in model_names
    ]
    return ModelListResponse(data=data)
