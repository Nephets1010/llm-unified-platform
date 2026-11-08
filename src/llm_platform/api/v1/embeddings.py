"""POST /v1/embeddings — OpenAI 兼容 Embeddings 接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ...adapters.registry import get_adapter_registry
from ...auth.service import AuthContext
from ...db.repositories.endpoint_repo import EndpointRepo
from ...deps import DbSession, get_current_auth
from ...errors import NoRouteError, ValidationError
from ...models.schemas import (
    EmbeddingData,
    EmbeddingRequest,
    EmbeddingResponse,
    UsageOut,
)

router = APIRouter(prefix="/v1", tags=["embeddings"])


@router.post("/embeddings", response_model=EmbeddingResponse)
async def create_embeddings(
    body: EmbeddingRequest,
    db: DbSession,
    auth: AuthContext = Depends(get_current_auth),
):
    """OpenAI 兼容 Embeddings 接口。"""
    if not auth.has_scope("embeddings") and not auth.has_scope("admin"):
        raise ValidationError(message="API Key 无 embeddings 权限", required_scope="embeddings")

    repo = EndpointRepo(db)
    candidates = repo.list_by_model(body.model)
    if not candidates:
        raise NoRouteError(message=f"模型 {body.model} 无可用端点", model=body.model)

    endpoint = candidates[0]
    adapter_registry = get_adapter_registry()
    adapter = adapter_registry.get(endpoint.provider) or adapter_registry.get_by_protocol(endpoint.protocol)
    if adapter is None:
        raise NoRouteError(message=f"无适配器: {endpoint.provider}")

    import os
    api_key = os.environ.get(endpoint.credential_env, "")

    raw = await adapter.embeddings(
        body.model, body.input, base_url=endpoint.base_url, api_key=api_key
    )

    data = [
        EmbeddingData(index=item.get("index", i), embedding=item.get("embedding", []))
        for i, item in enumerate(raw.get("data", []))
    ]
    usage_raw = raw.get("usage", {})
    return EmbeddingResponse(
        data=data,
        model=raw.get("model", body.model),
        usage=UsageOut(
            prompt_tokens=usage_raw.get("prompt_tokens", 0),
            completion_tokens=0,
            total_tokens=usage_raw.get("total_tokens", 0),
        ),
    )
