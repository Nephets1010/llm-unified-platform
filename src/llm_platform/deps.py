"""依赖注入：get_db / get_redis / get_settings / get_current_auth。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from .auth.service import AuthContext, AuthService
from .config import Settings
from .config import get_settings as _get_settings
from .db.session import get_db as _get_db
from .errors import AuthError

# ── 类型别名 ──
DbSession = Annotated[Session, Depends(_get_db)]
AppSettings = Annotated[Settings, Depends(_get_settings)]


def get_redis():  # noqa: ANN201
    """获取 Redis 客户端（懒加载，失败返回 None）。"""
    try:
        import redis

        settings = _get_settings()
        return redis.from_url(settings.redis_url, decode_responses=True)
    except Exception:
        return None


RedisClient = Annotated[object, Depends(get_redis)]


async def get_current_auth(
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> AuthContext:
    """统一鉴权依赖：业务面 API Key / 管理面 JWT 自动识别（fail-closed）。

    - Authorization: Bearer sk-... → API Key 鉴权
    - Authorization: Bearer <JWT> → JWT 鉴权
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthError(message="缺少 Authorization 头", reason="missing_token")

    token = authorization[7:]
    auth_service = AuthService(db)

    if token.startswith("sk-"):
        return auth_service.authenticate_api_key(token)
    else:
        return auth_service.authenticate_jwt(token)


async def require_admin(
    auth: Annotated[AuthContext, Depends(get_current_auth)],
) -> AuthContext:
    """要求 admin 角色。"""
    if auth.role != "admin" and not auth.has_scope("admin"):
        raise AuthError(message="需要 admin 权限", reason="insufficient_role")
    return auth


async def require_evaluator(
    auth: Annotated[AuthContext, Depends(get_current_auth)],
) -> AuthContext:
    """要求 evaluator 或 admin 角色。"""
    if auth.role not in ("admin", "evaluator"):
        raise AuthError(message="需要 evaluator 权限", reason="insufficient_role")
    return auth
