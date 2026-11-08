"""Prometheus 指标定义：QPS/延迟/错误/Token/成本/熔断/路由/限流/预算/评估。

monitoring 不反控业务（依赖铁律），只提供打点函数供上层调用。
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# ── 请求量 ──
REQUESTS_TOTAL = Counter(
    "llm_requests_total",
    "总请求数",
    ["method", "path", "status", "model", "provider"],
)

# ── 延迟 ──
REQUEST_LATENCY = Histogram(
    "llm_request_latency_seconds",
    "请求延迟（秒）",
    ["method", "path", "model"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

FIRST_TOKEN_LATENCY = Histogram(
    "llm_first_token_latency_seconds",
    "流式首 token 延迟（秒）",
    ["model"],
    buckets=(0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0),
)

# ── Token / 成本 ──
TOKENS_TOTAL = Counter(
    "llm_tokens_total",
    "Token 用量",
    ["model", "type"],
)

COST_TOTAL = Counter(
    "llm_cost_usd_total",
    "累计成本（USD）",
    ["tenant", "model"],
)

# ── 错误 ──
ERRORS_TOTAL = Counter(
    "llm_errors_total",
    "错误数",
    ["error_code", "model"],
)

# ── 熔断器 ──
CIRCUIT_STATE = Gauge(
    "llm_circuit_state",
    "熔断器状态（0=closed 1=open 2=half_open）",
    ["endpoint_id"],
)

# ── 限流 ──
RATE_LIMITED_TOTAL = Counter(
    "llm_rate_limited_total",
    "被限流的请求数",
    ["dimension"],
)

# ── 路由 ──
ROUTE_HITS_TOTAL = Counter(
    "llm_route_hits_total",
    "路由命中数",
    ["model", "provider", "strategy"],
)

FAILOVER_TOTAL = Counter(
    "llm_failover_total",
    "failover 触发次数",
    ["model", "reason"],
)

# ── 预算 ──
BUDGET_USED_RATIO = Gauge(
    "llm_budget_used_ratio",
    "预算使用比例（0~1+）",
    ["tenant"],
)

BUDGET_ALERTS_TOTAL = Counter(
    "llm_budget_alerts_total",
    "预算告警触发次数",
    ["tenant", "level"],
)

# ── 评估任务 ──
EVAL_TASKS_TOTAL = Counter(
    "llm_eval_tasks_total",
    "评测任务数",
    ["status"],
)

EVAL_TASK_DURATION = Histogram(
    "llm_eval_task_duration_seconds",
    "评测任务耗时（秒）",
    buckets=(1, 5, 10, 30, 60, 120, 300, 600),
)

# ── call_log 缓冲 ──
CALLLOG_DROPPED = Counter(
    "llm_calllog_dropped_total",
    "call_log 缓冲溢出丢弃数",
)

CALLLOG_FLUSHED = Counter(
    "llm_calllog_flushed_total",
    "call_log 批量写入成功数",
)

CALLLOG_FLUSH_ERRORS = Counter(
    "llm_calllog_flush_errors_total",
    "call_log 写入失败数",
)


# ── 打点函数 ──

def record_request(method: str, path: str, status: int, model: str, provider: str) -> None:
    REQUESTS_TOTAL.labels(method=method, path=path, status=str(status), model=model, provider=provider).inc()

def record_latency(method: str, path: str, model: str, seconds: float) -> None:
    REQUEST_LATENCY.labels(method=method, path=path, model=model).observe(seconds)

def record_first_token(model: str, seconds: float) -> None:
    FIRST_TOKEN_LATENCY.labels(model=model).observe(seconds)

def record_tokens(model: str, prompt_tokens: int, completion_tokens: int) -> None:
    TOKENS_TOTAL.labels(model=model, type="prompt").inc(prompt_tokens)
    TOKENS_TOTAL.labels(model=model, type="completion").inc(completion_tokens)

def record_cost(tenant: str, model: str, cost: float) -> None:
    COST_TOTAL.labels(tenant=tenant, model=model).inc(cost)

def record_error(error_code: str, model: str) -> None:
    ERRORS_TOTAL.labels(error_code=error_code, model=model).inc()

def record_rate_limited(dimension: str) -> None:
    RATE_LIMITED_TOTAL.labels(dimension=dimension).inc()

def record_route_hit(model: str, provider: str, strategy: str) -> None:
    ROUTE_HITS_TOTAL.labels(model=model, provider=provider, strategy=strategy).inc()

def record_failover(model: str, reason: str) -> None:
    FAILOVER_TOTAL.labels(model=model, reason=reason).inc()

def record_budget_used(tenant: str, ratio: float) -> None:
    BUDGET_USED_RATIO.labels(tenant=tenant).set(ratio)

def record_budget_alert(tenant: str, level: str) -> None:
    BUDGET_ALERTS_TOTAL.labels(tenant=tenant, level=level).inc()

def record_eval_task(status: str) -> None:
    EVAL_TASKS_TOTAL.labels(status=status).inc()

def record_eval_duration(seconds: float) -> None:
    EVAL_TASK_DURATION.observe(seconds)

def record_calllog_dropped() -> None:
    CALLLOG_DROPPED.inc()

def record_calllog_flushed(count: int) -> None:
    CALLLOG_FLUSHED.inc(count)

def record_calllog_flush_error() -> None:
    CALLLOG_FLUSH_ERRORS.inc()
