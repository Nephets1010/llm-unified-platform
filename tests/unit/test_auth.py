"""鉴权测试：合法/非法/吊销 Key、JWT 三档角色、fail-closed。"""

from __future__ import annotations

import uuid
from datetime import UTC

import pytest

from llm_platform.auth.api_key import generate_api_key, hash_api_key
from llm_platform.auth.jwt import create_access_token, decode_access_token
from llm_platform.auth.service import AuthService
from llm_platform.db.repositories.key_repo import KeyRepo
from llm_platform.db.repositories.tenant_repo import TenantRepo
from llm_platform.db.session import SessionLocal, init_db
from llm_platform.errors import AuthError


@pytest.fixture(autouse=True)
def setup_db():
    init_db()
    yield


class TestApiKeyGeneration:
    def test_generate_key_format(self):
        plaintext, key_hash, key_prefix = generate_api_key()
        assert plaintext.startswith("sk-")
        assert len(key_hash) == 64  # SHA-256 hex
        assert key_prefix == plaintext[:8]

    def test_hash_consistency(self):
        plaintext, key_hash, _ = generate_api_key()
        assert hash_api_key(plaintext) == key_hash

    def test_unique_keys(self):
        keys = {generate_api_key()[0] for _ in range(100)}
        assert len(keys) == 100


class TestJWT:
    def test_create_and_decode(self):
        token, expires_in = create_access_token(
            subject="1", tenant_id=10, role="admin", ttl_minutes=60
        )
        assert expires_in == 3600
        payload = decode_access_token(token)
        assert payload["sub"] == "1"
        assert payload["tenant_id"] == 10
        assert payload["role"] == "admin"

    def test_invalid_token(self):
        with pytest.raises(AuthError):
            decode_access_token("invalid-token")

    def test_expired_token(self):
        token, _ = create_access_token(
            subject="1", tenant_id=1, role="admin", ttl_minutes=0
        )
        # ttl_minutes=0 应立即过期（可能有时钟精度问题，用 -1 确保过期）
        from datetime import datetime, timedelta

        import jwt as pyjwt
        payload = {
            "sub": "1", "tenant_id": 1, "role": "admin",
            "exp": datetime.now(UTC) - timedelta(minutes=5),
        }
        expired = pyjwt.encode(payload, "test-secret", algorithm="HS256")
        # 设置 settings secret
        from llm_platform.config import get_settings
        settings = get_settings()
        original_secret = settings.jwt_secret
        settings.jwt_secret = "test-secret"
        try:
            with pytest.raises(AuthError):
                decode_access_token(expired)
        finally:
            settings.jwt_secret = original_secret


class TestAuthService:
    def test_authenticate_valid_key(self):
        db = SessionLocal()
        try:
            tenant_repo = TenantRepo(db)
            uid = uuid.uuid4().hex[:8]
            tenant = tenant_repo.create_tenant(f"test-auth-{uid}")
            project = tenant_repo.create_project(tenant.id, f"test-proj-{uid}")

            plaintext, key_hash, key_prefix = generate_api_key()
            key_repo = KeyRepo(db)
            key_repo.create_key(
                key_hash=key_hash,
                key_prefix=key_prefix,
                tenant_id=tenant.id,
                project_id=project.id,
                scopes=["chat", "admin"],
            )

            auth_service = AuthService(db)
            ctx = auth_service.authenticate_api_key(plaintext)
            assert ctx.tenant_id == tenant.id
            assert ctx.project_id == project.id
            assert ctx.has_scope("chat")
            assert ctx.has_scope("admin")
        finally:
            db.close()

    def test_authenticate_invalid_key(self):
        db = SessionLocal()
        try:
            auth_service = AuthService(db)
            with pytest.raises(AuthError):
                auth_service.authenticate_api_key("sk-invalid-key-12345")
        finally:
            db.close()

    def test_authenticate_revoked_key(self):
        db = SessionLocal()
        try:
            tenant_repo = TenantRepo(db)
            uid = uuid.uuid4().hex[:8]
            tenant = tenant_repo.create_tenant(f"test-revoke-{uid}")
            project = tenant_repo.create_project(tenant.id, f"test-revoke-proj-{uid}")

            plaintext, key_hash, key_prefix = generate_api_key()
            key_repo = KeyRepo(db)
            key = key_repo.create_key(
                key_hash=key_hash,
                key_prefix=key_prefix,
                tenant_id=tenant.id,
                project_id=project.id,
            )
            key_repo.revoke_key(key.id)

            auth_service = AuthService(db)
            with pytest.raises(AuthError):
                auth_service.authenticate_api_key(plaintext)
        finally:
            db.close()

    def test_authenticate_bad_format(self):
        db = SessionLocal()
        try:
            auth_service = AuthService(db)
            with pytest.raises(AuthError):
                auth_service.authenticate_api_key("invalid-format")
        finally:
            db.close()
