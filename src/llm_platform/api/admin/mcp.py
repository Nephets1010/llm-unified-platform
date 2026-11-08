"""MCP 工具管理 HTTP API（T12 遗留收口）。

提供工具注册、列表、调用能力，统一错误封套。
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ...auth.service import AuthContext
from ...db.session import get_db
from ...deps import require_admin
from ...errors import NotFoundError
from ...eval.mcp_gateway import McpGateway
from ...logging import get_logger

router = APIRouter(prefix="/admin/v1/mcp", tags=["mcp"])
logger = get_logger("api.admin.mcp")


# ── DTOs ──

class McpToolRegisterIn(BaseModel):
    """MCP 工具注册请求。"""

    name: str = Field(..., min_length=1, max_length=128)
    handler: str = Field(default="", max_length=256, description="本地处理器标识")
    input_schema: dict[str, Any] = Field(default_factory=dict)
    description: str = Field(default="", max_length=1024)
    auth_type: str = Field(default="none", max_length=16)
    credential_env: str | None = Field(default=None, max_length=64)


class McpToolOut(BaseModel):
    """MCP 工具信息（与 McpTool 实体字段对齐）。"""

    model_config = {"from_attributes": True}

    id: int
    name: str
    schema_: dict[str, Any] = Field(serialization_alias="schema")
    auth_type: str
    credential_env: str | None
    enabled: bool


class McpCallIn(BaseModel):
    """MCP 工具调用请求。"""

    tool_name: str = Field(..., min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


class McpCallOut(BaseModel):
    """MCP 工具调用结果。"""

    success: bool
    result: Any = None
    error: str | None = None
    latency_ms: int = 0


# ── 端点 ──

@router.post("/tools", response_model=McpToolOut, summary="注册 MCP 工具")
async def register_tool(
    body: McpToolRegisterIn,
    auth: AuthContext = Depends(require_admin),
    db=Depends(get_db),
) -> Any:
    """注册 MCP 工具。"""
    gateway = McpGateway(db)
    # 将 handler/description 合并入 schema，对齐 McpGateway.register_tool(name, schema, auth_type, credential_env)
    schema: dict[str, Any] = dict(body.input_schema)
    if body.handler:
        schema["handler"] = body.handler
    if body.description:
        schema["description"] = body.description

    tool = gateway.register_tool(
        name=body.name,
        schema=schema,
        auth_type=body.auth_type,
        credential_env=body.credential_env,
    )
    logger.info("mcp_tool_registered", tool_name=body.name, actor=str(auth.api_key_id))
    return tool


@router.get("/tools", response_model=list[McpToolOut], summary="列出 MCP 工具")
async def list_tools(
    auth: AuthContext = Depends(require_admin),  # noqa: ARG001
    db=Depends(get_db),
) -> Any:
    """列出全部 MCP 工具。"""
    gateway = McpGateway(db)
    return gateway.list_tools()


@router.post("/call", response_model=McpCallOut, summary="调用 MCP 工具")
async def call_tool(
    body: McpCallIn,
    auth: AuthContext = Depends(require_admin),
    db=Depends(get_db),
) -> Any:
    """调用 MCP 工具（写 call_log，source=mcp）。未知工具返回 404 NOT_FOUND_006。"""
    gateway = McpGateway(db)

    # 未知工具 → 404 统一信封（P1-11）
    tool = gateway.get_tool(body.tool_name)
    if tool is None:
        raise NotFoundError(
            message=f"MCP 工具不存在: {body.tool_name}",
            resource="mcp_tool",
            resource_id=body.tool_name,
        )

    start = time.perf_counter()
    try:
        result = gateway.call_tool(
            tool_name=body.tool_name,
            arguments=body.arguments,
            tenant_id=auth.tenant_id,
            project_id=auth.project_id,
        )
        latency_ms = int((time.perf_counter() - start) * 1000)
        return McpCallOut(
            success=result.get("success", False),
            result=result.get("result"),
            error=result.get("error"),
            latency_ms=result.get("latency_ms", latency_ms),
        )
    except Exception as exc:
        latency_ms = int((time.perf_counter() - start) * 1000)
        logger.warning("mcp_tool_call_failed", tool_name=body.tool_name, error=str(exc))
        return McpCallOut(success=False, error=str(exc), latency_ms=latency_ms)
