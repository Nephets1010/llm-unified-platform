"""错误信封与错误码测试。"""

from __future__ import annotations

from llm_platform.errors import (
    AuthError,
    ConfigError,
    ConflictError,
    NoRouteError,
    NotFoundError,
    PlatformError,
    QuotaExceededError,
    RateLimitError,
    RiskAlertError,
    UpstreamError,
    ValidationError,
    error_payload,
)
from llm_platform.logging import set_trace_id


class TestErrorCodes:
    def test_auth_error(self):
        e = AuthError()
        assert e.code == "AUTH_001"
        assert e.status_code == 401

    def test_quota_error(self):
        e = QuotaExceededError()
        assert e.code == "QUOTA_002"
        assert e.status_code == 429

    def test_route_error(self):
        e = NoRouteError()
        assert e.code == "ROUTE_003"
        assert e.status_code == 503

    def test_upstream_error(self):
        e = UpstreamError()
        assert e.code == "UPSTREAM_004"
        assert e.status_code == 502

    def test_validation_error(self):
        e = ValidationError()
        assert e.code == "VALID_005"
        assert e.status_code == 422

    def test_not_found_error(self):
        e = NotFoundError()
        assert e.code == "NOT_FOUND_006"
        assert e.status_code == 404

    def test_risk_error(self):
        e = RiskAlertError()
        assert e.code == "RISK_007"
        assert e.status_code == 409

    def test_rate_limit_error(self):
        e = RateLimitError()
        assert e.code == "RATE_008"
        assert e.status_code == 429

    def test_conflict_error(self):
        e = ConflictError()
        assert e.code == "CONFLICT_009"
        assert e.status_code == 409

    def test_config_error(self):
        e = ConfigError()
        assert e.code == "CONFIG_010"
        assert e.status_code == 400


class TestErrorPayload:
    def test_envelope_structure(self):
        set_trace_id("test-trace-id-123")
        e = AuthError(message="测试错误", reason="test")
        payload = error_payload(e)
        assert "error" in payload
        assert payload["error"]["code"] == "AUTH_001"
        assert payload["error"]["message"] == "测试错误"
        assert payload["error"]["trace_id"] == "test-trace-id-123"
        assert payload["error"]["context"] == {"reason": "test"}

    def test_no_context(self):
        e = PlatformError()
        payload = error_payload(e)
        assert payload["error"]["context"] is None
