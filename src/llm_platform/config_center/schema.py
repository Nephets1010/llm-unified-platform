"""配置 schema：各命名空间配置模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RoutingConfig(BaseModel):
    """路由配置。"""

    strategy: str = "health_aware"
    fallback_order: list[str] = Field(default_factory=lambda: ["cost_first", "weighted"])
    circuit: dict = Field(default_factory=lambda: {
        "error_rate_threshold": 0.5,
        "open_seconds": 30,
        "half_open_probes": 1,
    })


class LimitConfig(BaseModel):
    """限流配置。"""

    default_rpm: int = 60
    default_tpm: int = 60_000
    eval_concurrency: int = 4


class CanaryConfig(BaseModel):
    """灰度配置。"""

    canary_weight: int = 0  # 0-100
    subject_type: str = "tenant"
    subject_id: str = ""


class AlertConfig(BaseModel):
    """告警配置。"""

    budget_alert_ratio: float = 0.8
    error_rate_threshold: float = 0.05
    p95_latency_threshold_ms: float = 5000.0
