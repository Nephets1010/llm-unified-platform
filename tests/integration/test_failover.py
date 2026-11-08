"""failover 集成测试（T12）：验证熔断、故障切换、灰度路由。"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from llm_platform.errors import NoRouteError, UpstreamError
from llm_platform.routing.health import get_health_registry
from llm_platform.routing.router import Router


def _make_endpoint(id_: int, provider: str = "openai", priority: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        id=id_,
        name=f"ep-{id_}",
        provider=provider,
        protocol="openai",
        base_url="http://localhost:9999",
        model_name="gpt-4o",
        credential_env="TEST_KEY",
        priority=priority,
        weight=1.0,
        cost_per_1k_prompt=0.005,
        cost_per_1k_completion=0.015,
        is_active=True,
    )


class TestFailover:
    """failover 行为测试。"""

    def test_order_candidates_puts_available_first(self) -> None:
        registry = get_health_registry()
        router = Router()
        eps = [_make_endpoint(i) for i in (1, 2, 3)]
        # 端点 2 不可用
        for _ in range(5):
            registry.record_failure(2)

        ordered = router.order_candidates(eps)
        assert ordered[0].id != 2
        assert ordered[-1].id == 2

    @pytest.mark.asyncio
    async def test_failover_on_upstream_error(self) -> None:
        router = Router()
        eps = [_make_endpoint(i) for i in (20, 21)]

        call_count = 0

        async def failing_call(endpoint):
            nonlocal call_count
            call_count += 1
            if endpoint.id == 20:
                raise UpstreamError(message="模拟故障")
            return {"ok": True, "endpoint_id": endpoint.id}

        endpoint, result = await router.execute_with_failover(eps, failing_call)
        assert endpoint.id == 21
        assert result["ok"] is True
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_all_fail_raises_upstream_error(self) -> None:
        router = Router()
        eps = [_make_endpoint(i) for i in (30, 31)]

        async def always_fail(endpoint):
            raise UpstreamError(message="全部失败")

        with pytest.raises(UpstreamError):
            await router.execute_with_failover(eps, always_fail)

    @pytest.mark.asyncio
    async def test_no_available_raises_no_route(self) -> None:
        registry = get_health_registry()
        router = Router()
        eps = [_make_endpoint(i) for i in (10, 11)]
        # 全部熔断
        for ep in eps:
            for _ in range(10):
                registry.record_failure(ep.id)

        async def call(endpoint):
            return {}

        with pytest.raises(NoRouteError):
            await router.execute_with_failover(eps, call)


class TestCircuitBreaker:
    """熔断器状态机测试。"""

    def test_closed_to_open(self) -> None:
        registry = get_health_registry()
        # 通过 registry.record_failure 更新健康度，触发熔断
        for _ in range(6):
            registry.record_failure(200)
        breaker = registry.get_breaker(200)
        assert breaker.state == "open"

    def test_open_to_half_open_after_timeout(self) -> None:
        registry = get_health_registry()
        registry.configure(open_seconds=0.1)
        for _ in range(6):
            registry.record_failure(201)
        breaker = registry.get_breaker(201)
        assert breaker.state == "open"
        time.sleep(0.15)
        # 触发检查
        assert breaker.is_available() is True
        assert breaker.state == "half_open"

    def test_half_open_to_closed_on_success(self) -> None:
        registry = get_health_registry()
        registry.configure(open_seconds=0.1)
        # 用进程内唯一 ID 避免与其他测试的全局状态冲突
        eid = 300 + int(time.time() * 1000) % 10000
        for _ in range(6):
            registry.record_failure(eid)
        breaker = registry.get_breaker(eid)
        time.sleep(0.15)
        breaker.is_available()  # trigger half_open
        breaker.record_result(success=True)
        assert breaker.state == "closed"

    def test_half_open_to_open_on_failure(self) -> None:
        registry = get_health_registry()
        registry.configure(open_seconds=0.1, half_open_probes=1)
        for _ in range(6):
            registry.record_failure(203)
        breaker = registry.get_breaker(203)
        time.sleep(0.15)
        breaker.is_available()  # trigger half_open
        breaker.record_result(success=False)
        assert breaker.state == "open"


class TestCanaryRouting:
    """灰度路由测试。"""

    def test_canary_weight_zero_no_canary(self) -> None:
        router = Router()
        router.set_canary([41], 0.0)
        eps = [_make_endpoint(i) for i in (40, 41)]
        for _ in range(20):
            ep = router.select_endpoint(eps)
            assert ep.id == 40

    def test_canary_weight_one_all_canary(self) -> None:
        router = Router()
        router.set_canary([51], 1.0)
        eps = [_make_endpoint(i) for i in (50, 51)]
        for _ in range(20):
            ep = router.select_endpoint(eps)
            assert ep.id == 51

    def test_canary_weight_half_mixed(self) -> None:
        router = Router()
        router.set_canary([61], 0.5)
        eps = [_make_endpoint(i) for i in (60, 61)]
        canary_count = sum(1 for _ in range(100) if router.select_endpoint(eps).id == 61)
        assert 20 < canary_count < 80

    def test_clear_canary(self) -> None:
        router = Router()
        router.set_canary([71], 1.0)
        router.clear_canary()
        eps = [_make_endpoint(i) for i in (70, 71)]
        for _ in range(20):
            ep = router.select_endpoint(eps)
            assert ep.id == 70


class TestCallLogBufferRetry:
    """call_log 缓冲重试测试。"""

    @pytest.mark.asyncio
    async def test_flush_retry_on_failure(self) -> None:
        from llm_platform.gateway.buffer import CallLogBuffer

        buffer = CallLogBuffer(max_retries=3, retry_base_delay_s=0.01)
        call_count = 0

        def failing_flush(batch):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError("模拟 DB 故障")

        buffer.set_flush_fn(failing_flush)
        buffer.put({"test": 1})

        await buffer._flush_with_retry([{"test": 1}])
        # 第 3 次成功
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_flush_retry_exhausted_moves_to_buffer(self) -> None:
        from llm_platform.gateway.buffer import CallLogBuffer

        buffer = CallLogBuffer(max_retries=2, retry_base_delay_s=0.01)

        def always_fail(batch):
            raise RuntimeError("永久故障")

        buffer.set_flush_fn(always_fail)
        await buffer._flush_with_retry([{"test": 1}])
        # 重试耗尽后进入重试缓冲
        assert len(buffer._retry_buffer) == 1
