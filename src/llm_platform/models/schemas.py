"""Pydantic DTO：对应 api-contract.md 全部请求/响应 schema。

状态枚举常量统一定义于此，禁止魔法值散落。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

# ── 状态枚举常量 ──
STATUS_ENABLED = 1
STATUS_DISABLED = 0
STATUS_REVOKED = 0

CALL_STATUS_OK = "ok"
CALL_STATUS_ERROR = "error"
CALL_STATUS_TIMEOUT = "timeout"
CALL_STATUS_RATE_LIMITED = "rate_limited"

SOURCE_API = "api"
SOURCE_EVAL = "eval"
SOURCE_MCP = "mcp"

ROLE_ADMIN = "admin"
ROLE_CONSUMER = "consumer"
ROLE_EVALUATOR = "evaluator"

EVAL_STATUS_PENDING = "pending"
EVAL_STATUS_RUNNING = "running"
EVAL_STATUS_SUCCESS = "success"
EVAL_STATUS_FAILED = "failed"
EVAL_STATUS_CANCELLED = "cancelled"

CONFIG_STATUS_PENDING = "pending"
CONFIG_STATUS_APPLIED = "applied"
CONFIG_STATUS_ROLLED_BACK = "rolled_back"


# ════════════════════════════════════════════
# 业务面 /v1/*
# ════════════════════════════════════════════

class ChatMessageIn(BaseModel):
    role: str = Field(..., description="system / user / assistant")
    content: str = Field(..., description="消息内容")


class ChatCompletionRequest(BaseModel):
    model: str = Field(..., description="逻辑模型名（路由到模型池）")
    messages: list[ChatMessageIn] = Field(..., min_length=1)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, gt=0)
    stream: bool = Field(default=False)
    user: str | None = Field(default=None, description="终端用户标识")


class UsageOut(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatChoiceOut(BaseModel):
    index: int = 0
    message: ChatMessageIn
    finish_reason: str = "stop"


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[ChatChoiceOut]
    usage: UsageOut
    x_trace_id: str | None = None


class ModelInfo(BaseModel):
    id: str
    object: str = "model"
    created: int = 0
    owned_by: str = ""


class ModelListResponse(BaseModel):
    object: str = "list"
    data: list[ModelInfo]


class EmbeddingRequest(BaseModel):
    model: str
    input: str | list[str]


class EmbeddingData(BaseModel):
    object: str = "embedding"
    index: int = 0
    embedding: list[float]


class EmbeddingResponse(BaseModel):
    object: str = "list"
    data: list[EmbeddingData]
    model: str
    usage: UsageOut


# ════════════════════════════════════════════
# 管理面 /admin/v1/*
# ════════════════════════════════════════════

class TokenRequest(BaseModel):
    api_key: str = Field(..., description="管理员 API Key")
    ttl_minutes: int = Field(default=60, ge=1, le=1440)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    role: str


class TenantCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    monthly_budget: float = Field(default=1000.0, gt=0)


class TenantUpdate(BaseModel):
    monthly_budget: float | None = Field(default=None, gt=0)
    status: int | None = Field(default=None)


class TenantOut(BaseModel):
    id: int
    name: str
    monthly_budget: float
    status: int
    budget_used: float = 0.0
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    monthly_budget: float | None = Field(default=None, gt=0)


class ProjectOut(BaseModel):
    id: int
    tenant_id: int
    name: str
    monthly_budget: float | None = None
    status: int
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class ApiKeyCreate(BaseModel):
    name: str = Field(default="default", max_length=128)
    scopes: list[str] = Field(default_factory=lambda: ["chat"])
    rpm_limit: int = Field(default=60, gt=0)
    tpm_limit: int = Field(default=60_000, gt=0)
    expires_at: datetime | None = None


class ApiKeyOut(BaseModel):
    id: int
    key_prefix: str
    tenant_id: int
    project_id: int
    name: str
    scopes: list[str]
    status: int
    rpm_limit: int
    tpm_limit: int
    expires_at: datetime | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class ApiKeyCreatedOut(ApiKeyOut):
    key: str = Field(..., description="明文 Key，仅创建时返回一次！")


class EndpointCreate(BaseModel):
    provider: str = Field(..., max_length=32)
    protocol: str = Field(..., max_length=16)
    model_name: str = Field(..., max_length=128)
    base_url: str = Field(..., max_length=512)
    credential_env: str = Field(..., max_length=64)
    weight: float = Field(default=1.0, gt=0)
    priority: int = Field(default=100)
    canary_group: str = Field(default="stable", max_length=16)
    cost_per_1k_prompt: float = Field(default=0.0, ge=0)
    cost_per_1k_completion: float = Field(default=0.0, ge=0)


class EndpointUpdate(BaseModel):
    enabled: bool | None = None
    weight: float | None = Field(default=None, gt=0)
    priority: int | None = None
    canary_group: str | None = None
    cost_per_1k_prompt: float | None = Field(default=None, ge=0)
    cost_per_1k_completion: float | None = Field(default=None, ge=0)
    healthy: bool | None = None


class EndpointOut(BaseModel):
    id: int
    provider: str
    protocol: str
    model_name: str
    base_url: str
    credential_env: str
    enabled: bool
    healthy: bool
    weight: float
    priority: int
    canary_group: str
    cost_per_1k_prompt: float
    cost_per_1k_completion: float
    avg_latency_ms: float
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class CallLogItem(BaseModel):
    id: int
    trace_id: str
    tenant_id: int
    project_id: int
    model: str
    provider: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tokens_estimated: bool = False
    cost: float = 0.0
    latency_ms: int = 0
    first_token_latency_ms: int | None = None
    status: str = "ok"
    error_code: str | None = None
    source: str = "api"
    attempt_count: int = 1
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class PaginatedResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[Any]


class ErrorEnvelope(BaseModel):
    error: dict[str, Any]
