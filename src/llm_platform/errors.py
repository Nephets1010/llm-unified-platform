"""统一异常体系与错误码。

错误码格式：<域>_<3位编号>，如 AUTH_001。所有对外错误响应统一信封：
{"error": {"code", "message", "trace_id", "context"}}

错误码表（新增必须同步 docs/api-contract.md §0.2）：
  SYS_000       500  系统内部错误
  AUTH_001      401  鉴权失败
  QUOTA_002     429  配额/预算耗尽
  ROUTE_003     503  无可用路由
  UPSTREAM_004  502  上游调用失败
  VALID_005     422  参数校验失败
  NOT_FOUND_006 404  资源不存在
  RISK_007      409  风险熔断
  RATE_008      429  限流（区别于预算）
  CONFLICT_009  409  状态冲突
  CONFIG_010    400  配置非法
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from .logging import get_trace_id


class PlatformError(Exception):
    """业务基础异常。"""

    code: str = "SYS_000"
    status_code: int = 500
    message: str = "系统内部错误"

    def __init__(self, message: str | None = None, **context: Any) -> None:
        self.message = message or self.message
        self.context = context
        super().__init__(self.message)


class AuthError(PlatformError):
    code = "AUTH_001"
    status_code = 401
    message = "鉴权失败"


class QuotaExceededError(PlatformError):
    code = "QUOTA_002"
    status_code = 429
    message = "配额或预算已耗尽"


class NoRouteError(PlatformError):
    code = "ROUTE_003"
    status_code = 503
    message = "没有可用的模型路由"


class UpstreamError(PlatformError):
    code = "UPSTREAM_004"
    status_code = 502
    message = "上游模型调用失败"


class ValidationError(PlatformError):
    code = "VALID_005"
    status_code = 422
    message = "请求参数校验失败"


class NotFoundError(PlatformError):
    code = "NOT_FOUND_006"
    status_code = 404
    message = "资源不存在"


class RiskAlertError(PlatformError):
    code = "RISK_007"
    status_code = 409
    message = "风险规则触发熔断"


class RateLimitError(PlatformError):
    code = "RATE_008"
    status_code = 429
    message = "请求频率超限"


class ConflictError(PlatformError):
    code = "CONFLICT_009"
    status_code = 409
    message = "状态冲突"


class ConfigError(PlatformError):
    code = "CONFIG_010"
    status_code = 400
    message = "配置非法"


def error_payload(exc: PlatformError) -> dict[str, Any]:
    """构造统一错误响应体。"""
    return {
        "error": {
            "code": exc.code,
            "message": exc.message,
            "trace_id": get_trace_id(),
            "context": exc.context or None,
        }
    }


async def platform_error_handler(_request: Request, exc: PlatformError) -> JSONResponse:
    """PlatformError 全局处理器。"""
    return JSONResponse(status_code=exc.status_code, content=error_payload(exc))


async def unhandled_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """未捕获异常兜底处理器（禁止裸 500）。"""
    wrapped = PlatformError(message="系统内部错误")
    wrapped.context = {"type": type(exc).__name__}
    return JSONResponse(status_code=500, content=error_payload(wrapped))
