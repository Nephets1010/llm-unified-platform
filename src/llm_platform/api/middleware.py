"""HTTP 中间件：trace_id 生成/透传、访问日志、耗时统计。"""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from ..logging import get_logger, set_trace_id
from ..monitoring.metrics import record_latency, record_request
from ..utils.ids import generate_trace_id

logger = get_logger("middleware")


class TraceMiddleware(BaseHTTPMiddleware):
    """为每个请求生成 UUIDv4 trace_id，写入 ContextVar 和响应头 X-Request-Id。"""

    async def dispatch(self, request: Request, call_next) -> Response:  # noqa: ANN001
        trace_id = request.headers.get("X-Request-Id") or generate_trace_id()
        set_trace_id(trace_id)

        start = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - start

        # 注入 trace_id 响应头
        response.headers["X-Request-Id"] = trace_id

        # 访问日志（脱敏后输出）
        path = request.url.path
        logger.info(
            "http_request",
            method=request.method,
            path=path,
            status=response.status_code,
            latency_ms=round(elapsed * 1000, 2),
            source="middleware",
        )

        # Prometheus 打点
        record_request(request.method, path, response.status_code, "-", "-")
        record_latency(request.method, path, "-", elapsed)

        return response
