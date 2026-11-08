"""数据模型 v2（对应 docs/data-model.md）。

实体：Tenant / Project / ApiKey / ModelEndpoint / CallLog / RateLimitPolicy /
      EvalDataset / EvalDatasetItem / EvalTask / EvalReport / AuditLog /
      RiskAlert / ConfigRevision / McpTool
关系：Tenant 1-N Project/ApiKey/CallLog；Project 1-N ApiKey/CallLog；
      ModelEndpoint 1-N CallLog；EvalDataset 1-N Item/Task；EvalTask 1-N Report。
溯源：CallLog.trace_id / source；EvalReport.dataset_version / evaluator_version。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin

# SQLite 用 Integer 自增，PG 用 BigInteger（迁移脚本中处理）
BigIntPK = BigInteger().with_variant(Integer, "sqlite")


class Tenant(TimestampMixin, Base):
    """租户：三级隔离顶层。"""

    __tablename__ = "tenant"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    monthly_budget: Mapped[float] = mapped_column(Float, default=1000.0, nullable=False)
    status: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # 1=启用 0=停用

    projects: Mapped[list[Project]] = relationship(back_populates="tenant", lazy="selectin")
    api_keys: Mapped[list[ApiKey]] = relationship(back_populates="tenant", lazy="selectin")


class Project(TimestampMixin, Base):
    """项目：租户/项目/Key 三级隔离中间层。"""

    __tablename__ = "project"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_project_tenant_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    monthly_budget: Mapped[float | None] = mapped_column(Float, nullable=True)  # 空=继承租户
    status: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    tenant: Mapped[Tenant] = relationship(back_populates="projects")


class ApiKey(TimestampMixin, Base):
    """API Key：仅存 SHA-256 hash，明文仅创建时返回一次。"""

    __tablename__ = "api_key"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), default="default", nullable=False)
    scopes: Mapped[list] = mapped_column(JSON, default=lambda: ["chat"], nullable=False)
    status: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # 1=启用 0=吊销
    rpm_limit: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    tpm_limit: Mapped[int] = mapped_column(Integer, default=60_000, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped[Tenant] = relationship(back_populates="api_keys")


class ModelEndpoint(TimestampMixin, Base):
    """模型端点：多厂商接入点，credential_env 只存环境变量名。"""

    __tablename__ = "model_endpoint"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    protocol: Mapped[str] = mapped_column(String(16), nullable=False)  # openai/anthropic/ollama
    model_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    credential_env: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    healthy: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    canary_group: Mapped[str] = mapped_column(String(16), default="stable", nullable=False)
    cost_per_1k_prompt: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    cost_per_1k_completion: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    avg_latency_ms: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)


class CallLog(TimestampMixin, Base):
    """调用日志：按月 RANGE 分区（PG），SQLite 下为普通表。

    注意：生产 PG 分区表主键为 (id, created_at)，SQLAlchemy 声明保持 id 为主键，
    分区迁移在 0002 迁移脚本中单独处理。
    """

    __tablename__ = "call_log"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"), nullable=False, index=True)
    endpoint_id: Mapped[int | None] = mapped_column(ForeignKey("model_endpoint.id"), nullable=True)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_estimated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cost: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    first_token_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="ok", nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="api", nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    route_strategy: Mapped[str | None] = mapped_column(String(32), nullable=True)
    prompt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class RateLimitPolicy(TimestampMixin, Base):
    """限流策略：按 tenant / api_key / model 维度可配。"""

    __tablename__ = "rate_limit_policy"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subject_type: Mapped[str] = mapped_column(String(16), nullable=False)  # tenant/api_key/model
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    rpm_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    tpm_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class EvalDataset(TimestampMixin, Base):
    """评测基准集：版本化 + checksum 防篡改。"""

    __tablename__ = "eval_dataset"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(256), default="", nullable=False)


class EvalDatasetItem(TimestampMixin, Base):
    """评测样本条目。"""

    __tablename__ = "eval_dataset_item"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("eval_dataset.id"), nullable=False, index=True)
    input_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    expected_output: Mapped[str] = mapped_column(Text, default="", nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict, nullable=False)


class EvalTask(TimestampMixin, Base):
    """评测任务：状态机 pending→running→success/failed/cancelled。"""

    __tablename__ = "eval_task"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("eval_dataset.id"), nullable=False, index=True)
    models: Mapped[list] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class EvalReport(TimestampMixin, Base):
    """评测报告：每模型一行，聚合指标。"""

    __tablename__ = "eval_report"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("eval_task.id"), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    overall_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(32), nullable=False)
    evaluator_version: Mapped[str] = mapped_column(String(64), nullable=False)


class AuditLog(TimestampMixin, Base):
    """操作审计：所有管理操作与调用完成事件。"""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)  # api_key/jwt_user/system
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    resource_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class RiskAlert(TimestampMixin, Base):
    """风险告警。"""

    __tablename__ = "risk_alert"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    level: Mapped[str] = mapped_column(String(16), default="yellow", nullable=False)
    rule_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    subject_type: Mapped[str] = mapped_column(String(32), default="tenant", nullable=False)
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ConfigRevision(TimestampMixin, Base):
    """配置版本：全量快照 + 单调递增 version。"""

    __tablename__ = "config_revision"
    __table_args__ = (UniqueConstraint("namespace", "version", name="uq_config_ns_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    namespace: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class McpTool(TimestampMixin, Base):
    """MCP 工具注册表（P1-05 预留）。"""

    __tablename__ = "mcp_tool"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    schema_: Mapped[dict[str, Any]] = mapped_column("schema", JSON, nullable=False)
    auth_type: Mapped[str] = mapped_column(String(16), default="none", nullable=False)
    credential_env: Mapped[str | None] = mapped_column(String(64), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
