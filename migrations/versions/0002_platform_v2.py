"""platform v2: 新表 + 加列 + call_log 分区 + 数据迁移

Revision ID: 0002_platform_v2
Revises: 0001_initial
Create Date: 2026-01-08

动作：
- 新增表：project / rate_limit_policy / eval_dataset / eval_dataset_item /
  eval_report / audit_log / config_revision / mcp_tool
- 加列：api_key(+5) / model_endpoint(+3) / call_log(+5)
- call_log 转按月 RANGE 分区（PostgreSQL；SQLite 下保持普通表）
- 含完整 downgrade()
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_platform_v2"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    bind = op.get_bind()

    # ── 1. 新增表 ──

    op.create_table(
        "project",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("monthly_budget", sa.Float(), nullable=True),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_project_tenant_name"),
    )
    op.create_index("ix_project_tenant_id", "project", ["tenant_id"])

    op.create_table(
        "rate_limit_policy",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("subject_type", sa.String(16), nullable=False),
        sa.Column("subject_id", sa.String(64), nullable=False),
        sa.Column("rpm_limit", sa.Integer(), nullable=False),
        sa.Column("tpm_limit", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rate_limit_policy_subject", "rate_limit_policy", ["subject_id"])

    op.create_table(
        "eval_dataset",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("source", sa.String(256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version"),
    )
    op.create_index("ix_eval_dataset_name", "eval_dataset", ["name"])

    op.create_table(
        "eval_dataset_item",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("input_prompt", sa.Text(), nullable=False),
        sa.Column("expected_output", sa.Text(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["eval_dataset.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_eval_dataset_item_dataset_id", "eval_dataset_item", ["dataset_id"])

    op.create_table(
        "eval_report",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("overall_score", sa.Float(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("dataset_version", sa.String(32), nullable=False),
        sa.Column("evaluator_version", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["eval_task.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_eval_report_task_model", "eval_report", ["task_id", "model"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=True),
        sa.Column("actor_type", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.String(64), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("resource_type", sa.String(32), nullable=True),
        sa.Column("resource_id", sa.String(64), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("ip", sa.String(45), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_log_action", "audit_log", ["action"])
    op.create_index("ix_audit_log_request_id", "audit_log", ["request_id"])
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])
    op.create_index("ix_audit_log_tenant_created", "audit_log", ["tenant_id", "created_at"])

    op.create_table(
        "config_revision",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("namespace", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("namespace", "version", name="uq_config_ns_version"),
    )
    op.create_index("ix_config_revision_namespace", "config_revision", ["namespace"])

    op.create_table(
        "mcp_tool",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("schema", sa.JSON(), nullable=False),
        sa.Column("auth_type", sa.String(16), nullable=False),
        sa.Column("credential_env", sa.String(64), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    # ── 2. 加列：api_key ──
    op.add_column("api_key", sa.Column("key_prefix", sa.String(16), nullable=False, server_default="sk-"))
    op.add_column("api_key", sa.Column("project_id", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("api_key", sa.Column("scopes", sa.JSON(), nullable=False, server_default='["chat"]'))
    op.add_column("api_key", sa.Column("tpm_limit", sa.Integer(), nullable=False, server_default="60000"))
    op.add_column("api_key", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("api_key", sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("api_key", sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))

    # ── 3. 加列：model_endpoint ──
    op.add_column("model_endpoint", sa.Column("protocol", sa.String(16), nullable=False, server_default="openai"))
    op.add_column("model_endpoint", sa.Column("credential_env", sa.String(64), nullable=False, server_default=""))
    op.add_column("model_endpoint", sa.Column("canary_group", sa.String(16), nullable=False, server_default="stable"))

    # ── 4. 加列：call_log ──
    op.add_column("call_log", sa.Column("project_id", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("call_log", sa.Column("tokens_estimated", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("call_log", sa.Column("first_token_latency_ms", sa.Integer(), nullable=True))
    op.add_column("call_log", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("call_log", sa.Column("route_strategy", sa.String(32), nullable=True))
    op.add_column("call_log", sa.Column("prompt_hash", sa.String(64), nullable=True))

    # call_log 补充索引
    op.create_index("ix_call_log_project_created", "call_log", ["project_id", "created_at"])
    op.create_index("ix_call_log_model_created", "call_log", ["model", "created_at"])
    op.create_index("ix_call_log_endpoint_created", "call_log", ["endpoint_id", "created_at"])
    op.create_index("ix_call_log_status_created", "call_log", ["status", "created_at"])

    # ── 5. PostgreSQL: call_log 转分区（RANGE by created_at，按月） ──
    if _is_postgres():
        # 预建 2026 年 12 个月分区
        for month in range(1, 13):
            start = f"2026-{month:02d}-01"
            end_month = month + 1
            if end_month > 12:
                end = "2027-01-01"
            else:
                end = f"2026-{end_month:02d}-01"
            partition_name = f"call_log_2026_{month:02d}"
            op.execute(
                f"CREATE TABLE IF NOT EXISTS {partition_name} "
                f"PARTITION OF call_log FOR VALUES FROM ('{start}') TO ('{end}')"
            )

    # ── 6. eval_task 加列（v2 重构：抽出 eval_report 后补 dataset_id/models/status 等） ──
    op.add_column("eval_task", sa.Column("name", sa.String(128), nullable=False, server_default="unnamed"))
    op.add_column("eval_task", sa.Column("dataset_id", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("eval_task", sa.Column("models", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("eval_task", sa.Column("status", sa.String(16), nullable=False, server_default="pending"))
    op.add_column("eval_task", sa.Column("progress", sa.Float(), nullable=False, server_default="0"))
    op.add_column("eval_task", sa.Column("config", sa.JSON(), nullable=False, server_default="{}"))
    op.add_column("eval_task", sa.Column("created_by", sa.String(64), nullable=False, server_default="system"))
    op.add_column("eval_task", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("eval_task", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("eval_task", sa.Column("error", sa.Text(), nullable=True))


def downgrade() -> None:
    """完整回滚：删新表/新列/分区。"""

    # eval_task 回滚加列
    for col in ("name", "dataset_id", "models", "status", "progress", "config",
                "created_by", "started_at", "finished_at", "error"):
        op.drop_column("eval_task", col)

    # call_log 索引回滚
    op.drop_index("ix_call_log_status_created", "call_log")
    op.drop_index("ix_call_log_endpoint_created", "call_log")
    op.drop_index("ix_call_log_model_created", "call_log")
    op.drop_index("ix_call_log_project_created", "call_log")

    # call_log 列回滚
    for col in ("project_id", "tokens_estimated", "first_token_latency_ms",
                "attempt_count", "route_strategy", "prompt_hash"):
        op.drop_column("call_log", col)

    # model_endpoint 列回滚
    for col in ("protocol", "credential_env", "canary_group"):
        op.drop_column("model_endpoint", col)

    # api_key 列回滚
    for col in ("key_prefix", "project_id", "scopes", "tpm_limit",
                "expires_at", "last_used_at", "revoked_at"):
        op.drop_column("api_key", col)

    # 新表回滚
    op.drop_table("mcp_tool")
    op.drop_table("config_revision")
    op.drop_table("audit_log")
    op.drop_table("eval_report")
    op.drop_table("eval_dataset_item")
    op.drop_table("eval_dataset")
    op.drop_table("rate_limit_policy")
    op.drop_table("project")
