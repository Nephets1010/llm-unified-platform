"""端到端集成测试：认证→路由→适配→落库主链路。

使用 mock 上游，不打真实外网。
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from llm_platform.adapters.base import ChatChoice, ChatMessage, ChatResponse, Usage
from llm_platform.auth.api_key import generate_api_key
from llm_platform.db.repositories.endpoint_repo import EndpointRepo
from llm_platform.db.repositories.key_repo import KeyRepo
from llm_platform.db.repositories.tenant_repo import TenantRepo
from llm_platform.db.session import SessionLocal, init_db
from llm_platform.main import create_app


@pytest.fixture(scope="function")
def client():
    init_db()
    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="function")
def setup_data():
    """初始化测试数据：租户/项目/Key/端点。"""
    init_db()
    db = SessionLocal()
    try:
        uid = uuid.uuid4().hex[:8]
        tenant_repo = TenantRepo(db)
        tenant = tenant_repo.create_tenant(f"e2e-tenant-{uid}", monthly_budget=10000.0)
        project = tenant_repo.create_project(tenant.id, f"e2e-project-{uid}")

        plaintext, key_hash, key_prefix = generate_api_key()
        key_repo = KeyRepo(db)
        key = key_repo.create_key(
            key_hash=key_hash,
            key_prefix=key_prefix,
            tenant_id=tenant.id,
            project_id=project.id,
            scopes=["chat", "admin"],
        )

        endpoint_repo = EndpointRepo(db)
        endpoint = endpoint_repo.create_endpoint(
            provider="openai",
            protocol="openai",
            model_name=f"test-model-{uid}",
            base_url="http://mock-upstream/v1",
            credential_env="LLM_OPENAI_API_KEY",
            weight=1.0,
            priority=10,
            cost_per_1k_prompt=0.001,
            cost_per_1k_completion=0.002,
        )

        yield {
            "tenant": tenant,
            "project": project,
            "key_plaintext": plaintext,
            "key_id": key.id,
            "endpoint": endpoint,
            "model_name": f"test-model-{uid}",
        }
    finally:
        db.close()


class TestHealthEndpoints:
    def test_healthz(self, client):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestChatCompletions:
    def test_chat_non_stream(self, client, setup_data):
        """非流式 chat completions（mock 上游）。"""
        model_name = setup_data["model_name"]
        mock_response = ChatResponse(
            id="chatcmpl-test",
            model=model_name,
            choices=[
                ChatChoice(
                    message=ChatMessage(role="assistant", content="你好！"),
                    finish_reason="stop",
                )
            ],
            usage=Usage(prompt_tokens=10, completion_tokens=5),
            latency_ms=100,
        )

        with patch(
            "llm_platform.adapters.openai_adapter.OpenAIAdapter.chat",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            resp = client.post(
                "/v1/chat/completions",
                json={
                    "model": model_name,
                    "messages": [{"role": "user", "content": "你好"}],
                    "stream": False,
                },
                headers={"Authorization": f"Bearer {setup_data['key_plaintext']}"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["object"] == "chat.completion"
        assert data["model"] == model_name
        assert data["choices"][0]["message"]["content"] == "你好！"
        assert data["usage"]["total_tokens"] == 15
        assert "x_trace_id" in data

    def test_chat_unauthorized(self, client, setup_data):
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": setup_data["model_name"],
                "messages": [{"role": "user", "content": "你好"}],
            },
            headers={"Authorization": "Bearer sk-invalid-key"},
        )
        assert resp.status_code == 401
        data = resp.json()
        assert data["error"]["code"] == "AUTH_001"

    def test_chat_missing_auth(self, client, setup_data):
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": setup_data["model_name"],
                "messages": [{"role": "user", "content": "你好"}],
            },
        )
        assert resp.status_code == 401


class TestModelsEndpoint:
    def test_list_models(self, client, setup_data):
        resp = client.get(
            "/v1/models",
            headers={"Authorization": f"Bearer {setup_data['key_plaintext']}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["object"] == "list"
        assert len(data["data"]) >= 1


class TestAdminAuth:
    def test_exchange_token(self, client, setup_data):
        resp = client.post(
            "/admin/v1/auth/token",
            json={"api_key": setup_data["key_plaintext"], "ttl_minutes": 60},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["role"] == "admin"


class TestAdminTenants:
    def test_create_and_get_tenant(self, client, setup_data):
        # 先获取 JWT
        token_resp = client.post(
            "/admin/v1/auth/token",
            json={"api_key": setup_data["key_plaintext"]},
        )
        token = token_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 创建租户（唯一名）
        uid = uuid.uuid4().hex[:8]
        resp = client.post(
            "/admin/v1/tenants",
            json={"name": f"new-e2e-tenant-{uid}", "monthly_budget": 500.0},
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == f"new-e2e-tenant-{uid}"
        tenant_id = data["id"]

        # 查询租户
        resp = client.get(f"/admin/v1/tenants/{tenant_id}", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["name"] == f"new-e2e-tenant-{uid}"


class TestErrorEnvelope:
    def test_error_format(self, client, setup_data):
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": setup_data["model_name"],
                "messages": [{"role": "user", "content": "你好"}],
            },
            headers={"Authorization": "Bearer sk-invalid"},
        )
        assert resp.status_code == 401
        data = resp.json()
        assert "error" in data
        assert "code" in data["error"]
        assert "message" in data["error"]
        assert "trace_id" in data["error"]
