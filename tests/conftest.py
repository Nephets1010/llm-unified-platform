"""全局测试配置：注入 jwt_secret 等必要测试环境变量。"""

from __future__ import annotations

import pytest

from llm_platform.config import get_settings


@pytest.fixture(autouse=True)
def _setup_test_jwt_secret():
    """所有测试自动注入测试用 jwt_secret，避免 HMAC 空密钥错误。"""
    settings = get_settings()
    original = settings.jwt_secret
    if not original:
        settings.jwt_secret = "test-secret-for-qa-validation"
    yield
    settings.jwt_secret = original
