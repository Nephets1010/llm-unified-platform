"""MCP 工具网关（ADR-010 / P1-05）：工具注册、调用代理、审计。

调用以 source=mcp 落 call_log，纳入评测指标。
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..logging import get_logger, get_trace_id
from ..models.entities import McpTool
from ..models.schemas import SOURCE_MCP
from ..utils.masking import hash_value

logger = get_logger("eval.mcp_gateway")


class McpGateway:
    """MCP 工具网关：注册/调用/审计。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    def register_tool(
        self,
        name: str,
        schema: dict[str, Any],
        auth_type: str = "none",
        credential_env: str | None = None,
    ) -> McpTool:
        """注册 MCP 工具。"""
        tool = McpTool(
            name=name,
            schema_=schema,
            auth_type=auth_type,
            credential_env=credential_env,
            enabled=True,
        )
        self.db.add(tool)
        self.db.commit()
        self.db.refresh(tool)
        logger.info("mcp_tool_registered", tool_id=tool.id, name=name)
        return tool

    def get_tool(self, name: str) -> McpTool | None:
        stmt = select(McpTool).where(McpTool.name == name)
        return self.db.execute(stmt).scalar_one_or_none()

    def list_tools(self) -> list[McpTool]:
        return list(self.db.execute(select(McpTool)).scalars().all())

    def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        tenant_id: int,
        project_id: int,
    ) -> dict[str, Any]:
        """调用 MCP 工具（同步代理，source=mcp 落 call_log）。

        Args:
            tool_name: 工具名。
            arguments: 调用参数。
            tenant_id: 租户 ID。
            project_id: 项目 ID。

        Returns:
            {"result": ..., "latency_ms": ..., "success": bool, "error": str|None}
        """
        tool = self.get_tool(tool_name)
        if not tool:
            return {"result": None, "latency_ms": 0, "success": False, "error": f"工具不存在: {tool_name}"}
        if not tool.enabled:
            return {"result": None, "latency_ms": 0, "success": False, "error": f"工具已禁用: {tool_name}"}

        start = time.perf_counter()
        try:
            result = self._execute_tool(tool, arguments)
            latency_ms = int((time.perf_counter() - start) * 1000)
            success = True
            error = None
        except Exception as exc:
            latency_ms = int((time.perf_counter() - start) * 1000)
            result = None
            success = False
            error = str(exc)

        # source=mcp 落 call_log
        self._log_mcp_call(
            tenant_id=tenant_id,
            project_id=project_id,
            tool_name=tool_name,
            latency_ms=latency_ms,
            success=success,
            error=error,
        )

        return {"result": result, "latency_ms": latency_ms, "success": success, "error": error}

    def _execute_tool(self, tool: McpTool, arguments: dict[str, Any]) -> Any:
        """执行工具调用（M1 简化：本地函数调用，M3 接入 mcp-python-sdk）。

        按 schema 中的 "handler" 字段路由到注册的处理函数。
        """
        handler_name = tool.schema_.get("handler", "")
        if handler_name == "echo":
            return {"echo": arguments.get("message", "")}
        elif handler_name == "search":
            query = arguments.get("query", "")
            return {"results": [f"result-{i} for '{query}'" for i in range(3)]}
        else:
            raise ValueError(f"未注册的工具 handler: {handler_name}")

    def _log_mcp_call(
        self,
        tenant_id: int,
        project_id: int,
        tool_name: str,
        latency_ms: int,
        success: bool,
        error: str | None,
    ) -> None:
        """source=mcp 落 call_log。"""
        try:
            from ..db.repositories.call_log_repo import CallLogRepo

            record = {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "endpoint_id": None,
                "trace_id": get_trace_id(),
                "model": f"mcp:{tool_name}",
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "tokens_estimated": False,
                "cost": 0.0,
                "latency_ms": latency_ms,
                "first_token_latency_ms": None,
                "status": "ok" if success else "error",
                "error_code": None if success else "UPSTREAM_004",
                "source": SOURCE_MCP,
                "attempt_count": 1,
                "route_strategy": None,
                "prompt_hash": hash_value(tool_name)[:64],
            }
            CallLogRepo(self.db).insert_many([record])
        except Exception as exc:
            logger.warning("mcp_call_log_failed", error=str(exc))
