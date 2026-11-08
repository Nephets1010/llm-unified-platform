"""JWT 签发/校验：三档角色 admin / consumer / evaluator。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from ..config import get_settings
from ..errors import AuthError

ALGORITHM = "HS256"


def create_access_token(
    *,
    subject: str,
    tenant_id: int,
    role: str,
    ttl_minutes: int | None = None,
) -> tuple[str, int]:
    """签发 JWT。

    Args:
        subject: api_key_id。
        tenant_id: 租户 ID。
        role: admin / consumer / evaluator。
        ttl_minutes: 过期时间（分钟），默认取配置。

    Returns:
        (access_token, expires_in_seconds)
    """
    settings = get_settings()
    if not settings.jwt_secret:
        raise AuthError(message="JWT secret 未配置", reason="jwt_secret_missing")
    expire_min = ttl_minutes or settings.jwt_expire_minutes
    payload: dict[str, Any] = {
        "sub": subject,
        "tenant_id": tenant_id,
        "role": role,
        "exp": datetime.now(UTC) + timedelta(minutes=expire_min),
        "iat": datetime.now(UTC),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)
    return token, expire_min * 60


def decode_access_token(token: str) -> dict[str, Any]:
    """校验并解码 JWT，失败抛 AuthError（fail-closed）。"""
    settings = get_settings()
    if not settings.jwt_secret:
        raise AuthError(message="JWT secret 未配置", reason="jwt_secret_missing")
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise AuthError(message="JWT 已过期", reason="token_expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError(message="JWT 无效", reason="token_invalid") from exc

    if "role" not in payload or "tenant_id" not in payload:
        raise AuthError(message="JWT claims 不完整", reason="missing_claims")
    return payload
