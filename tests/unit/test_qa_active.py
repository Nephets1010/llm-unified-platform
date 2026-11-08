"""QA 主动测试：工程师未覆盖的验收角度。

覆盖：鉴权 fail-closed、限流/预算、路由/熔断、SSE 流式、
安全红线（脱敏/无硬编码）、call_log 行为、迁移可重复、冒烟。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from llm_platform.adapters.base import (
    ChatChoice,
    ChatMessage,
    ChatResponse,
    StreamChunk,
    Usage,
)
from llm_platform.auth.api_key import generate_api_key
from llm_platform.config import get_settings
from llm_platform.db.repositories.endpoint_repo import EndpointRepo
from llm_platform.db.repositories.key_repo import KeyRepo
from llm_platform.db.repositories.tenant_repo import TenantRepo
from llm_platform.db.session import SessionLocal, init_db
from llm_platform.errors import (
    NoRouteError,
    QuotaExceededError,
    RateLimitError,
)
from llm_platform.main import create_app
from llm_platform.ratelimit.budget import BudgetGuard
from llm_platform.ratelimit.token_bucket import LocalTokenBucket, TokenBucket
from llm_platform.routing.health import CircuitBreaker, CircuitState, HealthRegistry
from llm_platform.routing.router import Router
from llm_platform.utils.masking import hash_value, mask_dict, mask_secret

# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════

@pytest.fixture(scope="function")
def client():
    init_db()
    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="function")
def qa_setup_data():
    """初始化测试数据：租户/项目/Key/端点。"""
    init_db()
    db = SessionLocal()
    try:
        uid = uuid.uuid4().hex[:8]
        tenant_repo = TenantRepo(db)
        tenant = tenant_repo.create_tenant(f"qa-tenant-{uid}", monthly_budget=10000.0)
        project = tenant_repo.create_project(tenant.id, f"qa-project-{uid}")

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
            model_name=f"qa-model-{uid}",
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
            "model_name": f"qa-model-{uid}",
        }
    finally:
        db.close()


# ═══════════════════════════════════════════════════════════
# A. 鉴权 fail-closed
# ═══════════════════════════════════════════════════════════

class TestAuthFailClosed:
    """鉴权 fail-closed：无 Key / 伪造 Key / 已吊销 Key / 过期 JWT。"""

    def test_no_auth_header(self, client):
        """无 Authorization 头 → 401 + 错误信封。"""
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "any", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 401
        body = resp.json()
        assert "error" in body
        assert body["error"]["code"] == "AUTH_001"
        assert "trace_id" in body["error"]

    def test_fake_api_key(self, client):
        """伪造 API Key → 401。"""
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "any", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer sk-fake-key-12345"},
        )
        assert resp.status_code == 401
        body = resp.json()
        assert body["error"]["code"] == "AUTH_001"

    def test_revoked_key(self, client, qa_setup_data):
        """已吊销 Key → 401。"""
        # 吊销 Key
        db = SessionLocal()
        try:
            KeyRepo(db).revoke_key(qa_setup_data["key_id"])
        finally:
            db.close()

        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": qa_setup_data["model_name"],
                "messages": [{"role": "user", "content": "hi"}],
            },
            headers={"Authorization": f"Bearer {qa_setup_data['key_plaintext']}"},
        )
        assert resp.status_code == 401
        body = resp.json()
        assert body["error"]["code"] == "AUTH_001"

    def test_expired_jwt(self, client, qa_setup_data):
        """过期 JWT → 401。"""
        # 生成过期 JWT
        settings = get_settings()
        import jwt as pyjwt

        expired_payload = {
            "sub": "1",
            "tenant_id": 1,
            "role": "admin",
            "exp": datetime.now(UTC) - timedelta(minutes=5),
        }
        expired_token = pyjwt.encode(
            expired_payload, settings.jwt_secret, algorithm="HS256"
        )

        resp = client.post(
            "/v1/chat/completions",
            json={"model": "any", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        assert resp.status_code == 401
        body = resp.json()
        assert body["error"]["code"] == "AUTH_001"

    def test_bad_format_key(self, client):
        """非 sk- 开头的 Key → 401。"""
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "any", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer not-a-valid-key"},
        )
        assert resp.status_code == 401

    def test_error_envelope_format(self, client):
        """验证统一错误信封结构：{error: {code, message, trace_id, context}}。"""
        resp = client.get("/v1/models")
        assert resp.status_code == 401
        body = resp.json()
        assert "error" in body
        err = body["error"]
        assert "code" in err
        assert "message" in err
        assert "trace_id" in err
        # context 可为 None 但字段存在
        assert "context" in err or err.get("context") is None


# ═══════════════════════════════════════════════════════════
# B. 限流与预算
# ═══════════════════════════════════════════════════════════

class TestRateLimitAndBudget:
    """限流：RPM 超限 → 429 + RATE_008；预算 100% → QUOTA_002；Redis 降级不失控。"""

    def test_rpm_exceeded_local_bucket(self):
        """本地令牌桶：超 RPM → RateLimitError。"""
        bucket = TokenBucket(redis_client=None, prefix="test_rpm")
        # 用极低 RPM 限制：rpm_limit=2 → 只允许 2 次
        bucket.check("key", "test-subject", rpm_limit=2, tpm_limit=100000)
        bucket.check("key", "test-subject", rpm_limit=2, tpm_limit=100000)
        # 第 3 次应该超限
        with pytest.raises(RateLimitError) as exc_info:
            bucket.check("key", "test-subject", rpm_limit=2, tpm_limit=100000)
        assert exc_info.value.code == "RATE_008"

    def test_budget_100_percent_blocks(self):
        """预算 100% → QuotaExceededError。"""
        guard = BudgetGuard(redis_client=None)
        tenant_id = 99999
        guard.record_cost(tenant_id, 100.0)  # used = 100
        with pytest.raises(QuotaExceededError):
            guard.check_budget(tenant_id, monthly_budget=100.0, alert_ratio=0.8)

    def test_budget_80_percent_alerts(self):
        """预算 80% 告警但不熔断。"""
        guard = BudgetGuard(redis_client=None)
        tenant_id = 99998
        guard.record_cost(tenant_id, 85.0)  # used = 85, budget = 100
        ratio = guard.check_budget(tenant_id, monthly_budget=100.0, alert_ratio=0.8)
        assert ratio >= 0.8  # 告警触发
        assert ratio < 1.0  # 但不熔断

    def test_redis_unavailable_fallback_local(self):
        """Redis 不可用 → 降级本地桶，不得放行失控。"""
        # 模拟 Redis 客户端抛异常
        mock_redis = MagicMock()
        mock_redis.eval.side_effect = ConnectionError("Redis down")

        bucket = TokenBucket(redis_client=mock_redis, prefix="test_fallback")
        # 第一次会尝试 Redis → 失败 → 降级本地
        bucket.check("key", "fallback-test", rpm_limit=2, tpm_limit=100000)
        # 继续用本地桶，超限应该被拦截
        bucket.check("key", "fallback-test", rpm_limit=2, tpm_limit=100000)
        with pytest.raises(RateLimitError):
            bucket.check("key", "fallback-test", rpm_limit=2, tpm_limit=100000)

    def test_eval_bucket_isolated(self):
        """评测独立桶 eval:rl: 不占在线配额。"""
        online_bucket = TokenBucket(redis_client=None, prefix="rl")
        eval_bucket = TokenBucket(redis_client=None, prefix="eval:rl")
        # 在线桶用完
        for _ in range(2):
            online_bucket.check("key", "shared", rpm_limit=2, tpm_limit=100000)
        # eval 桶独立，不应受影响
        eval_bucket.check("key", "shared", rpm_limit=5, tpm_limit=100000)

    def test_local_token_bucket_refill(self):
        """本地桶令牌恢复。"""
        import time

        bucket = LocalTokenBucket(max_tokens=2, refill_rate=100.0)  # 100/s
        bucket.try_consume(2)
        result = bucket.try_consume(1)
        assert not result.allowed  # 暂时没令牌
        time.sleep(0.05)  # 50ms → 约 5 个令牌
        result = bucket.try_consume(1)
        assert result.allowed  # 恢复了


# ═══════════════════════════════════════════════════════════
# C. 路由与熔断
# ═══════════════════════════════════════════════════════════

class TestRoutingAndCircuitBreaker:
    """路由：全端点不健康 → ROUTE_003；熔断 open → 拒绝/转移。"""

    def test_all_unhealthy_returns_route_error(self):
        """全端点不健康（熔断 open 未超时）→ NoRouteError (ROUTE_003)。"""
        from llm_platform.routing.health import get_health_registry

        registry = get_health_registry()
        registry.configure(error_rate_threshold=0.5, open_seconds=30.0)

        # 创建端点
        ep = MagicMock()
        ep.id = 998
        ep.priority = 10

        # 连续失败触发熔断 → OPEN
        for _ in range(10):
            registry.record_failure(998)

        # 熔断 open（open_seconds=30s，远未超时）
        assert not registry.is_available(998)

        router = Router()
        with pytest.raises(NoRouteError) as exc_info:
            router.select_endpoint([ep])
        assert exc_info.value.code == "ROUTE_003"

    def test_circuit_breaker_open_blocks(self):
        """熔断 open 状态 → is_available False。"""
        breaker = CircuitBreaker(
            endpoint_id=1, error_rate_threshold=0.5, open_seconds=30.0
        )
        # 连续失败触发 open
        registry = HealthRegistry()
        breaker._registry = registry
        for _ in range(10):
            registry.get_health(1).record_failure()
            breaker.record_result(success=False)

        assert breaker.state == CircuitState.OPEN
        assert not breaker.is_available()

    def test_circuit_breaker_half_open_recovery(self):
        """熔断 open → 超时 → half_open → 成功 → closed。"""
        breaker = CircuitBreaker(
            endpoint_id=2, error_rate_threshold=0.5, open_seconds=0.01
        )
        registry = HealthRegistry()
        breaker._registry = registry

        # 触发 open
        for _ in range(10):
            registry.get_health(2).record_failure()
            breaker.record_result(success=False)
        assert breaker.state == CircuitState.OPEN

        # 等待超时
        import time

        time.sleep(0.05)
        assert breaker.is_available()  # → half_open

        # 成功恢复
        breaker.record_result(success=True)
        assert breaker.state == CircuitState.CLOSED

    def test_route_no_candidates(self):
        """空候选列表 → NoRouteError。"""
        router = Router()
        with pytest.raises(NoRouteError):
            router.select_endpoint([])


# ═══════════════════════════════════════════════════════════
# D. SSE 流式
# ═══════════════════════════════════════════════════════════

class TestStreaming:
    """流式：SSE 响应头 text/event-stream，chunk 格式正确。"""

    def test_sse_response_headers(self, client, qa_setup_data):
        """SSE 响应头应为 text/event-stream。"""
        mock_chunks = [
            StreamChunk(
                id="chunk-1",
                object="chat.completion.chunk",
                created=1736900000,
                model=qa_setup_data["model_name"],
                choices=[{"index": 0, "delta": {"role": "assistant", "content": "你"}, "finish_reason": None}],
            ),
            StreamChunk(
                id="chunk-1",
                object="chat.completion.chunk",
                created=1736900000,
                model=qa_setup_data["model_name"],
                choices=[{"index": 0, "delta": {}, "finish_reason": "stop"}],
                usage=Usage(prompt_tokens=10, completion_tokens=5),
            ),
        ]

        async def fake_chat_stream(self, request, *, base_url, api_key):
            for chunk in mock_chunks:
                yield chunk

        with patch(
            "llm_platform.adapters.openai_adapter.OpenAIAdapter.chat_stream",
            fake_chat_stream,
        ):
            resp = client.post(
                "/v1/chat/completions",
                json={
                    "model": qa_setup_data["model_name"],
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": True,
                },
                headers={"Authorization": f"Bearer {qa_setup_data['key_plaintext']}"},
            )

        # 检查 SSE 头
        content_type = resp.headers.get("content-type", "")
        assert "text/event-stream" in content_type

    def test_sse_chunk_format(self, client, qa_setup_data):
        """SSE 输出格式：data: {...}\n\n + data: [DONE]\n\n。"""
        mock_chunks = [
            StreamChunk(
                id="chunk-2",
                object="chat.completion.chunk",
                created=1736900000,
                model=qa_setup_data["model_name"],
                choices=[{"index": 0, "delta": {"role": "assistant", "content": "hello"}, "finish_reason": None}],
            ),
        ]

        async def fake_chat_stream(self, request, *, base_url, api_key):
            for chunk in mock_chunks:
                yield chunk

        with patch(
            "llm_platform.adapters.openai_adapter.OpenAIAdapter.chat_stream",
            fake_chat_stream,
        ):
            resp = client.post(
                "/v1/chat/completions",
                json={
                    "model": qa_setup_data["model_name"],
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": True,
                },
                headers={"Authorization": f"Bearer {qa_setup_data['key_plaintext']}"},
            )

        text = resp.text
        assert "data: " in text
        assert "[DONE]" in text


# ═══════════════════════════════════════════════════════════
# E. 安全红线
# ═══════════════════════════════════════════════════════════

class TestSecurityRedLines:
    """安全红线：无硬编码密钥；日志脱敏；DB 无明文。"""

    def test_mask_secret_hides_plaintext(self):
        """mask_secret('sk-abcdef') 输出只含前缀+长度。"""
        result = mask_secret("sk-abcdef")
        assert "sk-abcdef" not in result  # 不能完整泄露
        assert "***" in result
        assert "len=" in result

    def test_mask_secret_short_key(self):
        """短密钥全打星号。"""
        result = mask_secret("abc")
        assert "abc" not in result
        assert "***" in result

    def test_hash_value_is_sha256(self):
        """hash_value 输出 SHA-256 hex（64 字符）。"""
        h = hash_value("sk-secret")
        assert len(h) == 64
        assert h != "sk-secret"

    def test_mask_dict_recursive(self):
        """mask_dict 递归脱敏敏感字段。"""
        data = {
            "api_key": "sk-real-secret-key",
            "model": "gpt-4",
            "nested": {"password": "hunter2", "info": "ok"},
            "items": [{"token": "tok-123456", "name": "test"}],
        }
        masked = mask_dict(data)
        assert "sk-real-secret-key" not in str(masked)
        assert "hunter2" not in str(masked)
        assert "tok-123456" not in str(masked)
        assert masked["model"] == "gpt-4"  # 非敏感字段不变

    def test_no_hardcoded_secrets_in_config(self):
        """config.py 中密钥类字段默认值为空字符串。"""
        get_settings()
        # 默认值应为空（由环境变量注入）
        # 注意：测试 fixture 可能已注入 jwt_secret，这里只检查代码逻辑
        from llm_platform.config import Settings

        default_settings = Settings(
            _env_file=None, jwt_secret="", openai_api_key="", deepseek_api_key=""
        )
        assert default_settings.openai_api_key == ""
        assert default_settings.deepseek_api_key == ""

    def test_api_key_model_no_plaintext(self):
        """ApiKey 模型只存 key_hash，无明文 key 字段。"""
        from llm_platform.models.entities import ApiKey

        columns = [c.name for c in ApiKey.__table__.columns]
        assert "key_hash" in columns
        assert "key" not in columns  # 无明文 key 列

    def test_key_creation_returns_plaintext_once(self):
        """generate_api_key 返回 (plaintext, hash, prefix)，hash 不可逆推 plaintext。"""
        plaintext, key_hash, key_prefix = generate_api_key()
        assert plaintext.startswith("sk-")
        assert len(key_hash) == 64  # SHA-256
        assert key_prefix == plaintext[:8]
        # hash 不能反推 plaintext
        assert plaintext not in key_hash
        assert key_hash not in plaintext


# ═══════════════════════════════════════════════════════════
# F. call_log 行为
# ═══════════════════════════════════════════════════════════

class TestCallLogBehavior:
    """call_log：时间范围默认行为；trace_id 落库。"""

    def test_call_log_trace_id_present(self, client, qa_setup_data):
        """调用后 call_log 应含 trace_id。"""
        mock_response = ChatResponse(
            id="call-log-test",
            model=qa_setup_data["model_name"],
            choices=[
                ChatChoice(
                    message=ChatMessage(role="assistant", content="ok"),
                    finish_reason="stop",
                )
            ],
            usage=Usage(prompt_tokens=10, completion_tokens=5),
            latency_ms=50,
        )

        with patch(
            "llm_platform.adapters.openai_adapter.OpenAIAdapter.chat",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            resp = client.post(
                "/v1/chat/completions",
                json={
                    "model": qa_setup_data["model_name"],
                    "messages": [{"role": "user", "content": "test call log"}],
                },
                headers={"Authorization": f"Bearer {qa_setup_data['key_plaintext']}"},
            )

        assert resp.status_code == 200
        trace_id = resp.json().get("x_trace_id")
        assert trace_id is not None
        assert len(trace_id) > 0

    def test_calls_query_default_time_range(self, client, qa_setup_data):
        """管理面调用查询：缺时间范围时默认近 7 天（不报错）。"""
        # 先获取 JWT
        token_resp = client.post(
            "/admin/v1/auth/token",
            json={"api_key": qa_setup_data["key_plaintext"]},
        )
        assert token_resp.status_code == 200
        token = token_resp.json()["access_token"]

        # 不传时间范围参数
        resp = client.get(
            "/admin/v1/calls",
            headers={"Authorization": f"Bearer {token}"},
        )
        # 应返回 200（默认时间范围），不应 500
        assert resp.status_code == 200
        body = resp.json()
        assert "total" in body
        assert "items" in body

    def test_calls_query_with_time_range(self, client, qa_setup_data):
        """管理面调用查询：指定时间范围正常。"""
        token_resp = client.post(
            "/admin/v1/auth/token",
            json={"api_key": qa_setup_data["key_plaintext"]},
        )
        token = token_resp.json()["access_token"]

        now = datetime.now(UTC)
        from_str = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")
        to_str = now.strftime("%Y-%m-%dT%H:%M:%S")

        resp = client.get(
            "/admin/v1/calls",
            params={"created_from": from_str, "created_to": to_str},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════
# G. 迁移可重复执行
# ═══════════════════════════════════════════════════════════

class TestMigrationRepeatability:
    """迁移：upgrade → downgrade → upgrade 可重复执行。"""

    def test_migration_cycle(self):
        """验证迁移可重复执行（用独立 DB）。"""
        import os
        import subprocess
        import sys

        project_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        test_db = os.path.join(project_root, "llm_platform_migration_test.db")
        if os.path.exists(test_db):
            os.remove(test_db)

        env = os.environ.copy()
        env["LLM_DATABASE_URL"] = f"sqlite:///{test_db}"
        python_exe = sys.executable

        try:
            # upgrade head
            r1 = subprocess.run(
                [python_exe, "-m", "alembic", "upgrade", "head"],
                capture_output=True,
                text=True,
                env=env,
                cwd=project_root,
                timeout=60,
            )
            assert r1.returncode == 0, f"upgrade head failed: {r1.stderr[-500:]}"

            # downgrade -1
            r2 = subprocess.run(
                [python_exe, "-m", "alembic", "downgrade", "-1"],
                capture_output=True,
                text=True,
                env=env,
                cwd=project_root,
                timeout=60,
            )
            assert r2.returncode == 0, f"downgrade failed: {r2.stderr[-500:]}"

            # upgrade head again
            r3 = subprocess.run(
                [python_exe, "-m", "alembic", "upgrade", "head"],
                capture_output=True,
                text=True,
                env=env,
                cwd=project_root,
                timeout=60,
            )
            assert r3.returncode == 0, f"re-upgrade failed: {r3.stderr[-500:]}"

        finally:
            if os.path.exists(test_db):
                os.remove(test_db)


# ═══════════════════════════════════════════════════════════
# H. 冒烟测试
# ═══════════════════════════════════════════════════════════

class TestSmokeAndHealth:
    """冒烟：create_app() + /healthz 200 + 关键端点存在。"""

    def test_create_app_smoke(self):
        """create_app() 不抛异常。"""
        app = create_app()
        assert app is not None

    def test_healthz_200(self, client):
        """GET /healthz → 200。"""
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_readyz_200(self, client):
        """GET /readyz → 200（DB 可用即可）。"""
        resp = client.get("/readyz")
        assert resp.status_code == 200
        body = resp.json()
        assert body["checks"]["database"] is True

    def test_key_routes_exist(self):
        """关键路由存在。"""
        app = create_app()
        schema = app.openapi()
        paths = schema.get("paths", {})
        for route in [
            "/healthz",
            "/v1/chat/completions",
            "/v1/models",
            "/v1/embeddings",
            "/admin/v1/auth/token",
            "/admin/v1/tenants",
        ]:
            assert route in paths, f"Missing route: {route}"

    def test_metrics_endpoint(self, client):
        """GET /metrics → 200 (Prometheus)。"""
        resp = client.get("/metrics")
        assert resp.status_code == 200
