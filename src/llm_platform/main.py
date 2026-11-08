"""FastAPI 应用入口：装配、路由挂载、异常 handler、生命周期。"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .api.middleware import TraceMiddleware
from .config import get_settings
from .errors import PlatformError, platform_error_handler, unhandled_error_handler
from .logging import configure_logging, get_logger

logger = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN201
    """应用生命周期：启动/停止钩子。"""
    settings = get_settings()
    configure_logging(
        debug=settings.debug,
        level=settings.log_level,
        sample_rate=settings.log_sample_rate,
    )
    logger.info("app_starting", app=settings.app_name, env=settings.environment)
    yield
    logger.info("app_stopping")


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用。"""
    settings = get_settings()
    app = FastAPI(
        title="LLM Unified Platform",
        description="企业级大模型统一接入与能力评估中台",
        version="0.1.0",
        lifespan=lifespan,
    )

    # ── 中间件 ──
    app.add_middleware(TraceMiddleware)

    # ── 异常处理器 ──
    app.add_exception_handler(PlatformError, platform_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    # ── 非功能接口 ──
    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict:
        """存活探针（进程级）。"""
        return {"status": "ok"}

    @app.get("/readyz", tags=["health"])
    async def readyz() -> JSONResponse:
        """就绪探针（DB/Redis 连通性）。"""
        checks = {"database": False, "redis": False}

        try:
            from sqlalchemy import text

            from .db.session import engine

            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            checks["database"] = True
        except Exception:
            pass

        try:
            import redis as redis_lib

            r = redis_lib.from_url(settings.redis_url, socket_connect_timeout=1)
            r.ping()
            checks["redis"] = True
        except Exception:
            pass

        ready = all(checks.values()) or checks["database"]  # DB 必须，Redis 可选
        return JSONResponse(
            status_code=200 if ready else 503,
            content={"status": "ready" if ready else "not_ready", "checks": checks},
        )

    @app.get("/metrics", tags=["health"])
    async def metrics():  # noqa: ANN201
        """Prometheus 指标。"""
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
        from starlette.responses import Response

        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    # ── 业务面路由 ──
    from .api.v1.chat import router as chat_router
    from .api.v1.embeddings import router as embeddings_router
    from .api.v1.models import router as models_router

    app.include_router(chat_router)
    app.include_router(models_router)
    app.include_router(embeddings_router)

    # ── 管理面路由 ──
    from .api.admin.alerts import router as alerts_router
    from .api.admin.api_keys import router as api_keys_router
    from .api.admin.auth import router as auth_router
    from .api.admin.billing import router as billing_router
    from .api.admin.calls import router as calls_router
    from .api.admin.config import router as config_router
    from .api.admin.endpoints import router as endpoints_router
    from .api.admin.evals import router as evals_router
    from .api.admin.mcp import router as mcp_router
    from .api.admin.tenants import router as tenants_router

    app.include_router(auth_router)
    app.include_router(tenants_router)
    app.include_router(api_keys_router)
    app.include_router(endpoints_router)
    app.include_router(calls_router)
    app.include_router(evals_router)
    app.include_router(billing_router)
    app.include_router(alerts_router)
    app.include_router(config_router)
    app.include_router(mcp_router)

    return app


# uvicorn 入口：llm_platform.main:app
app = create_app()
