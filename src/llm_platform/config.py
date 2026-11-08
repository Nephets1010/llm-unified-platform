"""配置管理：全部敏感项经环境变量注入，禁止硬编码密钥。

配置键统一 LLM_ 前缀（pydantic-settings 自动映射），密钥类无内置真值。
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置：从环境变量 / .env 加载，LLM_ 前缀。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="LLM_",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── 基础 ──
    app_name: str = "llm-unified-platform"
    environment: str = Field(default="dev", description="dev / staging / prod")
    debug: bool = False

    # ── 存储 ──
    database_url: str = "sqlite:///./llm_platform.db"
    redis_url: str = "redis://localhost:6379/0"

    # ── 安全 ──
    jwt_secret: str = Field(default="", description="JWT 签名密钥，生产必须注入")
    jwt_expire_minutes: int = 60
    default_admin_key: str = Field(default="", description="初始管理员 API Key（仅开发用）")

    # ── 上游厂商（密钥只存环境变量，model_endpoint 仅存 credential_env 名） ──
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    anthropic_api_key: str = ""
    anthropic_base_url: str = "https://api.anthropic.com"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    qwen_api_key: str = ""
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    ollama_base_url: str = "http://localhost:11434"

    # ── 网关行为 ──
    request_timeout_s: float = 60.0
    max_retries: int = 2
    default_monthly_budget: float = 1000.0

    # ── 限流与预算 ──
    rate_limit_default_rpm: int = 60
    rate_limit_default_tpm: int = 60_000
    budget_alert_ratio: float = 0.8

    # ── 路由 ──
    routing_strategy: str = "health_aware"
    routing_failover_max: int = 2
    circuit_error_rate_threshold: float = 0.5
    circuit_open_seconds: float = 30.0
    circuit_half_open_probes: int = 1

    # ── 日志 ──
    log_level: str = "INFO"
    log_sample_rate: float = 1.0

    # ── 风险阈值 ──
    risk_budget_burn_ratio: float = 0.8
    risk_p95_latency_ms: float = 5000.0
    risk_error_rate: float = 0.05

    # ── call_log 缓冲 ──
    call_log_buffer_size: int = 10_000
    call_log_flush_interval_s: float = 1.0
    call_log_flush_batch: int = 500

    @field_validator("jwt_secret")
    @classmethod
    def _prod_requires_secret(cls, v: str, info) -> str:  # noqa: ANN001
        env = info.data.get("environment", "dev")
        if env == "prod" and not v:
            raise ValueError("生产环境必须通过 LLM_JWT_SECRET 注入密钥")
        return v


@lru_cache
def get_settings() -> Settings:
    """返回全局配置单例（缓存）。"""
    return Settings()
