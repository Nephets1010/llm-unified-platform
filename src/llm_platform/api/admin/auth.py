"""POST /admin/v1/auth/token — API Key 换取 JWT。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from ...auth.audit import audit_log
from ...auth.service import AuthService
from ...deps import DbSession
from ...models.schemas import TokenRequest, TokenResponse

router = APIRouter(prefix="/admin/v1/auth", tags=["admin-auth"])


@router.post("/token", response_model=TokenResponse)
async def exchange_token(
    body: TokenRequest,
    request: Request,
    db: DbSession,
):
    """API Key 换取 JWT（管理面鉴权入口）。"""
    auth_service = AuthService(db)
    access_token, expires_in, role = auth_service.exchange_token(
        body.api_key, ttl_minutes=body.ttl_minutes
    )

    audit_log(
        db,
        actor_type="api_key",
        actor_id="system",
        action="auth.token_exchange",
        ip=request.client.host if request.client else None,
        detail={"ttl_minutes": body.ttl_minutes, "role": role},
    )

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=expires_in,
        role=role,
    )
