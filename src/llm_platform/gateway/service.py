"""主链路编排 GatewayService：鉴权→限流→路由→适配→计量。

依赖方向：api → gateway → routing → adapters（严格单向）。
"""

from __future__ import annotations

import time
from typing import Any

from ..adapters.base import ChatRequest, ChatResponse, Usage
from ..adapters.registry import get_adapter_registry
from ..auth.service import AuthContext
from ..config import get_settings
from ..db.repositories.call_log_repo import CallLogRepo
from ..db.repositories.endpoint_repo import EndpointRepo
from ..errors import NoRouteError, PlatformError, QuotaExceededError, UpstreamError
from ..logging import get_logger, get_trace_id
from ..models.schemas import (
    CALL_STATUS_ERROR,
    CALL_STATUS_OK,
    CALL_STATUS_RATE_LIMITED,
    SOURCE_API,
)
from ..monitoring.metrics import (
    record_cost,
    record_error,
    record_first_token,
    record_latency,
    record_tokens,
)
from ..ratelimit.budget import BudgetGuard
from ..ratelimit.token_bucket import TokenBucket
from ..routing.health import get_health_registry
from ..routing.router import Router
from ..utils.masking import hash_value
from .buffer import CallLogBuffer

logger = get_logger("gateway.service")


class GatewayService:
    """主链路编排服务：一次调用的完整生命周期。"""

    def __init__(
        self,
        db,  # noqa: ANN001
        redis_client=None,
    ) -> None:
        self.db = db
        self.settings = get_settings()
        self.router = Router()
        self.token_bucket = TokenBucket(redis_client=redis_client, prefix="rl")
        self.budget_guard = BudgetGuard(redis_client=redis_client)
        self.call_log_buffer = CallLogBuffer(
            max_size=self.settings.call_log_buffer_size,
            flush_interval_s=self.settings.call_log_flush_interval_s,
            flush_batch=self.settings.call_log_flush_batch,
        )
        self._endpoint_repo = EndpointRepo(db)
        self._call_log_repo = CallLogRepo(db)

    async def start(self) -> None:
        """启动后台服务。"""
        await self.call_log_buffer.start()

    async def stop(self) -> None:
        """停止后台服务。"""
        await self.call_log_buffer.stop()

    async def chat_completions(
        self,
        request: ChatRequest,
        auth: AuthContext,
    ) -> ChatResponse | Any:
        """非流式 chat completions 主链路。

        Args:
            request: 统一请求。
            auth: 鉴权上下文。

        Returns:
            ChatResponse（非流式）或 StreamingResponse（流式）。
        """
        start = time.perf_counter()
        get_trace_id()
        status = CALL_STATUS_OK
        error_code: str | None = None
        endpoint = None
        response: ChatResponse | None = None
        prompt_hash = hash_value(
            "|".join(m.content for m in request.messages)
        )[:64]

        try:
            # 1. 预算检查
            tenant = self._get_tenant(auth.tenant_id)
            if tenant:
                self.budget_guard.check_budget(
                    auth.tenant_id,
                    tenant.monthly_budget,
                    self.settings.budget_alert_ratio,
                )

            # 2. 限流检查（三级：tenant / key / model）
            self._check_rate_limits(auth, request)

            # 3. 路由选端点
            candidates = self._endpoint_repo.list_by_model(request.model)
            if not candidates:
                raise NoRouteError(message=f"模型 {request.model} 无可用端点", model=request.model)

            # 4. 适配器调用（带 failover）
            endpoint, response = await self.router.execute_with_failover(
                candidates,
                lambda ep: self._call_adapter(ep, request),
            )

            # 5. 计量
            usage = response.usage
            cost = self._calculate_cost(endpoint, usage.prompt_tokens, usage.completion_tokens)
            self.budget_guard.record_cost(auth.tenant_id, cost)

            record_tokens(request.model, usage.prompt_tokens, usage.completion_tokens)
            record_cost(str(auth.tenant_id), request.model, cost)

            # 记录成功健康度
            get_health_registry().record_success(endpoint.id, response.latency_ms)

        except QuotaExceededError as exc:
            status = CALL_STATUS_RATE_LIMITED
            error_code = exc.code
            record_error(exc.code, request.model)
            self._log_call(auth, request, None, start, status, error_code, prompt_hash, 1)
            raise
        except PlatformError as exc:
            status = CALL_STATUS_ERROR
            error_code = exc.code
            record_error(exc.code, request.model)
            self._log_call(auth, request, endpoint, start, status, error_code, prompt_hash, 1)
            raise

        int((time.perf_counter() - start) * 1000)
        self._log_call(auth, request, endpoint, start, status, error_code, prompt_hash, 1, response=response)
        record_latency("POST", "/v1/chat/completions", request.model, time.perf_counter() - start)

        return response

    async def chat_completions_stream(
        self,
        request: ChatRequest,
        auth: AuthContext,
    ):
        """流式 chat completions：返回 StreamChunk 异步迭代器。"""
        start = time.perf_counter()
        get_trace_id()
        prompt_hash = hash_value(
            "|".join(m.content for m in request.messages)
        )[:64]

        # 预算检查
        tenant = self._get_tenant(auth.tenant_id)
        if tenant:
            self.budget_guard.check_budget(
                auth.tenant_id, tenant.monthly_budget, self.settings.budget_alert_ratio
            )

        # 限流检查
        self._check_rate_limits(auth, request)

        # 路由
        candidates = self._endpoint_repo.list_by_model(request.model)
        if not candidates:
            raise NoRouteError(message=f"模型 {request.model} 无可用端点", model=request.model)

        registry = get_health_registry()
        available = [e for e in candidates if registry.is_available(e.id)]
        if not available:
            raise NoRouteError(message="所有端点均不可用")

        endpoint = self.router.select_endpoint(candidates)
        adapter_registry = get_adapter_registry()
        adapter = adapter_registry.get(endpoint.provider) or adapter_registry.get_by_protocol(endpoint.protocol)
        if adapter is None:
            raise NoRouteError(message=f"无适配器: {endpoint.provider}", provider=endpoint.provider)

        api_key = self._get_credential(endpoint.credential_env)
        first_token_time: float | None = None
        total_content = ""
        stream_usage: Usage | None = None

        async def stream_wrapper() -> Any:
            nonlocal first_token_time, total_content, stream_usage
            try:
                async for chunk in adapter.chat_stream(
                    request, base_url=endpoint.base_url, api_key=api_key
                ):
                    if first_token_time is None:
                        first_token_time = time.perf_counter()
                        record_first_token(request.model, first_token_time - start)

                    # 累积 content 用于计量
                    for choice in chunk.choices:
                        delta = choice.get("delta", {})
                        if "content" in delta:
                            total_content += delta["content"]

                    # 捕获 usage（最后一个 chunk）
                    if chunk.usage:
                        stream_usage = chunk.usage

                    yield chunk

                # 流式结束：估算 tokens（若上游未提供 usage）
                if stream_usage is None:
                    est_prompt = sum(len(m.content) // 4 for m in request.messages)
                    est_completion = len(total_content) // 4
                    stream_usage = Usage(
                        prompt_tokens=est_prompt,
                        completion_tokens=est_completion,
                        tokens_estimated=True,
                    )

                # 记录成功健康度
                latency_ms = int((time.perf_counter() - start) * 1000)
                get_health_registry().record_success(endpoint.id, latency_ms)

                # 流式 call_log 落库（含 trace_id）
                self._log_stream_call(
                    auth, request, endpoint, start, stream_usage, prompt_hash, first_token_time
                )

            except Exception as exc:
                get_health_registry().record_failure(endpoint.id)
                logger.error("stream_error", error=str(exc))
                # 失败也落 call_log
                self._log_stream_call(
                    auth, request, endpoint, start, None, prompt_hash, first_token_time,
                    status=CALL_STATUS_ERROR, error_code="UPSTREAM_004",
                )
                raise

        return stream_wrapper(), endpoint, prompt_hash

    def _check_rate_limits(self, auth: AuthContext, request: ChatRequest) -> None:
        """三级限流检查：tenant / key / model。"""
        estimated_tokens = sum(len(m.content) // 4 for m in request.messages)

        # Key 级限流
        key_rpm = auth.scopes and self.settings.rate_limit_default_rpm or self.settings.rate_limit_default_rpm
        self.token_bucket.check(
            "key", str(auth.api_key_id), key_rpm, self.settings.rate_limit_default_tpm,
            token_cost=estimated_tokens,
        )

        # Tenant 级限流
        self.token_bucket.check(
            "tenant", str(auth.tenant_id),
            self.settings.rate_limit_default_rpm * 10,
            self.settings.rate_limit_default_tpm * 10,
            token_cost=estimated_tokens,
        )

        # Model 级限流
        self.token_bucket.check(
            "model", request.model,
            self.settings.rate_limit_default_rpm * 5,
            self.settings.rate_limit_default_tpm * 5,
            token_cost=estimated_tokens,
        )

    async def _call_adapter(self, endpoint, request: ChatRequest) -> ChatResponse:  # noqa: ANN001
        """调用适配器。"""
        adapter_registry = get_adapter_registry()
        adapter = adapter_registry.get(endpoint.provider) or adapter_registry.get_by_protocol(endpoint.protocol)
        if adapter is None:
            raise UpstreamError(message=f"无适配器: {endpoint.provider}", provider=endpoint.provider)

        api_key = self._get_credential(endpoint.credential_env)
        return await adapter.chat(request, base_url=endpoint.base_url, api_key=api_key)

    def _get_credential(self, credential_env: str) -> str:
        """按环境变量名获取密钥（不存密钥本体，ADR-009）。"""
        import os

        key = os.environ.get(credential_env, "")
        if not key:
            # 回退到 Settings 中的对应字段
            settings = get_settings()
            attr = credential_env.lower().removeprefix("llm_")
            key = getattr(settings, attr, "")
        return key

    def _get_tenant(self, tenant_id: int):  # noqa: ANN202
        from ..db.repositories.tenant_repo import TenantRepo

        return TenantRepo(self.db).get_tenant(tenant_id)

    def _calculate_cost(self, endpoint, prompt_tokens: int, completion_tokens: int) -> float:  # noqa: ANN001
        if endpoint is None:
            return 0.0
        return (
            prompt_tokens / 1000.0 * endpoint.cost_per_1k_prompt
            + completion_tokens / 1000.0 * endpoint.cost_per_1k_completion
        )

    def _log_call(
        self,
        auth: AuthContext,
        request: ChatRequest,
        endpoint,  # noqa: ANN001
        start: float,  # noqa: ARG002
        status: str,
        error_code: str | None,
        prompt_hash: str,
        attempt_count: int,
        response: ChatResponse | None = None,
    ) -> None:
        """记录 call_log（异步批量写入）。"""
        latency_ms = int((time.perf_counter() - start) * 1000)
        usage = response.usage if response else None
        cost = 0.0
        if endpoint and usage:
            cost = self._calculate_cost(endpoint, usage.prompt_tokens, usage.completion_tokens)

        record: dict[str, Any] = {
            "tenant_id": auth.tenant_id,
            "project_id": auth.project_id,
            "endpoint_id": endpoint.id if endpoint else None,
            "trace_id": get_trace_id(),
            "model": request.model,
            "prompt_tokens": usage.prompt_tokens if usage else 0,
            "completion_tokens": usage.completion_tokens if usage else 0,
            "tokens_estimated": usage.tokens_estimated if usage else False,
            "cost": cost,
            "latency_ms": latency_ms,
            "first_token_latency_ms": None,
            "status": status,
            "error_code": error_code,
            "source": SOURCE_API,
            "attempt_count": attempt_count,
            "route_strategy": self.router.strategy.name,
            "prompt_hash": prompt_hash,
        }
        self.call_log_buffer.put(record)

    def _log_stream_call(
        self,
        auth: AuthContext,
        request: ChatRequest,
        endpoint,  # noqa: ANN001
        start: float,
        usage: Usage | None,
        prompt_hash: str,
        first_token_time: float | None = None,
        status: str = CALL_STATUS_OK,
        error_code: str | None = None,
    ) -> None:
        """记录流式 call_log（异步批量写入）。"""
        latency_ms = int((time.perf_counter() - start) * 1000)
        first_token_latency = (
            int((first_token_time - start) * 1000) if first_token_time else None
        )
        cost = 0.0
        if endpoint and usage:
            cost = self._calculate_cost(endpoint, usage.prompt_tokens, usage.completion_tokens)

        record: dict[str, Any] = {
            "tenant_id": auth.tenant_id,
            "project_id": auth.project_id,
            "endpoint_id": endpoint.id if endpoint else None,
            "trace_id": get_trace_id(),
            "model": request.model,
            "prompt_tokens": usage.prompt_tokens if usage else 0,
            "completion_tokens": usage.completion_tokens if usage else 0,
            "tokens_estimated": usage.tokens_estimated if usage else True,
            "cost": cost,
            "latency_ms": latency_ms,
            "first_token_latency_ms": first_token_latency,
            "status": status,
            "error_code": error_code,
            "source": SOURCE_API,
            "attempt_count": 1,
            "route_strategy": self.router.strategy.name,
            "prompt_hash": prompt_hash,
        }
        self.call_log_buffer.put(record)
