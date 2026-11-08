# ============================================================
# Dockerfile — 多阶段构建（gateway + worker 同镜像）
# ============================================================

FROM python:3.11-slim AS builder

WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir hatchling && \
    pip install --no-cache-dir --no-build-isolation -e . 2>/dev/null || \
    pip install --no-cache-dir \
        fastapi uvicorn pydantic pydantic-settings sqlalchemy alembic \
        "psycopg[binary]" redis httpx structlog prometheus-client \
        apscheduler pyjwt argon2-cffi tiktoken

FROM python:3.11-slim

WORKDIR /app
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY src/ src/
COPY migrations/ migrations/
COPY alembic.ini ./

# 非 root 用户
RUN useradd --create-home appuser
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')"

CMD ["uvicorn", "llm_platform.main:app", "--host", "0.0.0.0", "--port", "8000"]
