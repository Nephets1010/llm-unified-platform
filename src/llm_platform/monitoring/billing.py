"""成本核算：账单聚合/查询/CSV 导出（P1-08，自核算口径）。"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..logging import get_logger

logger = get_logger("monitoring.billing")


class BillingService:
    """账单服务：按 tenant/project/model/day 聚合。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    def summary(
        self,
        tenant_id: int | None = None,
        project_id: int | None = None,
        month: str | None = None,
    ) -> dict:
        """账单汇总（month 格式 YYYY-MM）。"""
        from ..models.entities import CallLog

        if month is None:
            month = datetime.now(timezone.utc).strftime("%Y-%m")

        stmt = select(
            CallLog.model,
            func.coalesce(func.sum(CallLog.cost), 0.0).label("total_cost"),
            func.coalesce(func.sum(CallLog.prompt_tokens), 0).label("total_prompt"),
            func.coalesce(func.sum(CallLog.completion_tokens), 0).label("total_completion"),
        )
        if tenant_id is not None:
            stmt = stmt.where(CallLog.tenant_id == tenant_id)
        if project_id is not None:
            stmt = stmt.where(CallLog.project_id == project_id)
        # 月份过滤（兼容 SQLite/PG）
        month_pattern = f"{month}%"
        stmt = stmt.where(func.strftime("%Y-%m", CallLog.created_at) == month if self.db.bind.dialect.name == "sqlite" else func.to_char(CallLog.created_at, "YYYY-MM") == month)
        stmt = stmt.group_by(CallLog.model)

        rows = self.db.execute(stmt).all()
        by_model = [
            {
                "model": r.model,
                "cost_usd": round(r.total_cost, 6),
                "prompt_tokens": int(r.total_prompt),
                "completion_tokens": int(r.total_completion),
                "tokens": int(r.total_prompt + r.total_completion),
            }
            for r in rows
        ]

        total_cost = sum(m["cost_usd"] for m in by_model)
        total_prompt = sum(m["prompt_tokens"] for m in by_model)
        total_completion = sum(m["completion_tokens"] for m in by_model)

        # 按项目聚合
        stmt_proj = select(
            CallLog.project_id,
            func.coalesce(func.sum(CallLog.cost), 0.0).label("total_cost"),
        )
        if tenant_id is not None:
            stmt_proj = stmt_proj.where(CallLog.tenant_id == tenant_id)
        stmt_proj = stmt_proj.where(func.strftime("%Y-%m", CallLog.created_at) == month if self.db.bind.dialect.name == "sqlite" else func.to_char(CallLog.created_at, "YYYY-MM") == month)
        stmt_proj = stmt_proj.group_by(CallLog.project_id)

        by_project = [
            {"project_id": r.project_id, "cost_usd": round(r.total_cost, 6)}
            for r in self.db.execute(stmt_proj).all()
        ]

        return {
            "month": month,
            "tenant_id": tenant_id,
            "total_cost_usd": round(total_cost, 6),
            "total_prompt_tokens": total_prompt,
            "total_completion_tokens": total_completion,
            "by_model": by_model,
            "by_project": by_project,
        }

    def export_csv(
        self,
        tenant_id: int | None = None,
        month: str | None = None,
    ) -> str:
        """导出 CSV 格式账单。"""
        data = self.summary(tenant_id=tenant_id, month=month)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["model", "cost_usd", "prompt_tokens", "completion_tokens", "total_tokens"])
        for item in data["by_model"]:
            writer.writerow([
                item["model"],
                item["cost_usd"],
                item["prompt_tokens"],
                item["completion_tokens"],
                item["tokens"],
            ])
        return output.getvalue()

    def export_json(
        self,
        tenant_id: int | None = None,
        month: str | None = None,
    ) -> dict:
        """导出 JSON 格式账单。"""
        return self.summary(tenant_id=tenant_id, month=month)
