"""统一鉴权编排：fail-closed，业务面 API Key + 管理面 JWT。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.repositories.key_repo import KeyRepo
from ..errors import AuthError
from ..logging import get_logger
from .api_key import hash_api_key
from .jwt import decode_access_token

logger = get_logger("auth")


@dataclass
class AuthContext:
    """鉴权上下文（注入到路由依赖）。"""

    tenant_id: int
    project_id: int
    api_key_id: int
    scopes: list[str] = field(default_factory=list)
    role: str = "consumer"  # JWT 场景：admin/consumer/evaluator
    is_jwt: bool = False

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes or "admin" in self.scopes


class AuthService:
    """鉴权服务：唯一入口，任何新路由必须挂载。"""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.key_repo = KeyRepo(db)
        self.settings = get_settings()

    def authenticate_api_key(self, plaintext: str) -> AuthContext:
        """业务面鉴权：API Key（fail-closed）。

        校验流程：格式 → hash 查库 → status/expires_at/scope 校验。
        """
        if not plaintext or not plaintext.startswith("sk-"):
            raise AuthError(message="API Key 格式无效", reason="invalid_format")

        key_hash = hash_api_key(plaintext)
        key = self.key_repo.get_key_by_hash(key_hash)

        if key is None:
            raise AuthError(message="API Key 不存在", reason="key_not_found")

        if key.status != 1:
            raise AuthError(message="API Key 已吊销", reason="key_revoked")

        if key.expires_at and key.expires_at < datetime.now(UTC):
            raise AuthError(message="API Key 已过期", reason="key_expired")

        # 更新 last_used_at（异步语义，同步写入简化）
        self.key_repo.update_last_used(key.id)

        return AuthContext(
            tenant_id=key.tenant_id,
            project_id=key.project_id,
            api_key_id=key.id,
            scopes=list(key.scopes or []),
            role="consumer",
            is_jwt=False,
        )

    def authenticate_jwt(self, token: str) -> AuthContext:
        """管理面鉴权：JWT（fail-closed）。"""
        payload = decode_access_token(token)
        return AuthContext(
            tenant_id=int(payload["tenant_id"]),
            project_id=0,
            api_key_id=int(payload.get("sub", 0)),
            scopes=["admin"] if payload.get("role") == "admin" else [],
            role=payload.get("role", "consumer"),
            is_jwt=True,
        )

    def exchange_token(self, api_key: str, ttl_minutes: int = 60) -> tuple[str, int, str]:
        """API Key 换取 JWT（管理面 /admin/v1/auth/token）。

        Returns:
            (access_token, expires_in_seconds, role)
        """
        ctx = self.authenticate_api_key(api_key)
        if not ctx.has_scope("admin"):
            raise AuthError(message="API Key 无 admin 权限", reason="insufficient_scope")

        from .jwt import create_access_token

        token, expires_in = create_access_token(
            subject=str(ctx.api_key_id),
            tenant_id=ctx.tenant_id,
            role="admin",
            ttl_minutes=ttl_minutes,
        )
        return token, expires_in, "admin"
