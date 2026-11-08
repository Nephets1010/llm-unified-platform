# 关键接口定义（API Contract）— 大模型统一接入与能力评估中台

| 项 | 内容 |
| --- | --- |
| 文档版本 | v1.0 |
| 基础路径 | 业务面 `/v1/*`（OpenAI 兼容）；管理面 `/admin/v1/*` |
| 通用鉴权 | 业务面：`Authorization: Bearer sk-...`（API Key）；管理面：`Authorization: Bearer <JWT>` |
| 通用响应头 | `X-Request-Id`（= trace_id，全链路）；限流响应头 `X-RateLimit-Limit / Remaining / Reset` |
| 时间格式 | ISO 8601 UTC |
| 内容类型 | `application/json`；流式为 `text/event-stream` |

---

## 0. 通用约定

### 0.1 错误响应信封（全接口统一）
```json
{
  "error": {
    "code": "AUTH_001",
    "message": "鉴权失败",
    "trace_id": "b3c1f0a2-...",
    "context": {"reason": "key revoked"}
  }
}
```

### 0.2 错误码表
| 错误码 | HTTP | 含义 | 触发场景 |
| --- | --- | --- | --- |
| AUTH_001 | 401 | 鉴权失败 | Key 无效/过期/吊销、JWT 过期或角色不足 |
| QUOTA_002 | 429 | 配额/预算耗尽 | 租户月度预算 ≥100% |
| ROUTE_003 | 503 | 无可用路由 | 模型池无健康端点 |
| UPSTREAM_004 | 502 | 上游调用失败 | 厂商超时/5xx 且 failover 用尽 |
| VALID_005 | 422 | 参数校验失败 | 请求体不合法 |
| NOT_FOUND_006 | 404 | 资源不存在 | 管理面 ID 不存在 |
| RISK_007 | 409 | 风险熔断 | 风险规则触发熔断 |
| RATE_008 | 429 | 限流 | RPM/TPM 超限（区别于预算） |
| CONFLICT_009 | 409 | 状态冲突 | 重复创建、非法状态迁移 |
| CONFIG_010 | 400 | 配置非法 | 配置命名空间/负载校验失败 |

### 0.3 限流语义
| 面 | 限流维度 | 默认值 | 超限行为 |
| --- | --- | --- | --- |
| `/v1/*` | API Key RPM（60）/ TPM（60000）+ 租户预算 | 可配（rate_limit_policy） | `429 RATE_008`，`Retry-After: <秒>` |
| `/admin/v1/*` | JWT 主体 RPM 300 | 固定 | `429 RATE_008` |
| 评测调用 | `eval:` 独立桶（并发 ≤ config） | 任务配置 | 排队而非拒绝 |

### 0.4 分页约定
列表接口统一 query：`page=1&page_size=20&sort=-created_at`；响应：`{"total": 123, "page": 1, "page_size": 20, "items": [...]}`。

---

## 1. OpenAI 兼容面

### 1.1 POST /v1/chat/completions
- **鉴权**：API Key（scope: `chat`）
- **限流**：按 Key RPM/TPM + 租户预算
- **请求**：
```json
{
  "model": "deepseek-chat",
  "messages": [
    {"role": "system", "content": "你是助手"},
    {"role": "user", "content": "你好"}
  ],
  "temperature": 0.7,
  "max_tokens": 1024,
  "stream": false,
  "user": "end-user-id"
}
```
| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| model | string | 是 | 逻辑模型名（路由到模型池） |
| messages | array | 是 | `{role, content}` |
| temperature | float | 否 | 默认 0.7 |
| max_tokens | int | 否 | |
| stream | bool | 否 | 默认 false |
| user | string | 否 | 终端用户标识，写入审计 |

- **响应（非流式，200）**：
```json
{
  "id": "chatcmpl-9f3a...",
  "object": "chat.completion",
  "created": 1736900000,
  "model": "deepseek-chat",
  "choices": [
    {"index": 0, "message": {"role": "assistant", "content": "你好！"}, "finish_reason": "stop"}
  ],
  "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
  "x_trace_id": "b3c1f0a2-..."
}
```
- **响应（流式 SSE）**：`Content-Type: text/event-stream`
```
data: {"id":"chatcmpl-9f3a...","object":"chat.completion.chunk","created":1736900000,"model":"deepseek-chat","choices":[{"index":0,"delta":{"role":"assistant","content":"你"},"finish_reason":null}]}

data: {"id":"chatcmpl-9f3a...","object":"chat.completion.chunk","created":1736900000,"model":"deepseek-chat","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":12,"completion_tokens":8,"total_tokens":20}}

data: [DONE]
```
- **错误**：401 AUTH_001 / 429 QUOTA_002、RATE_008 / 422 VALID_005 / 502 UPSTREAM_004 / 503 ROUTE_003
- **备注**：OpenAI SDK 零改造可用；`usage` 在流式场景可能为估算值（`tokens_estimated=true` 落 call_log，响应体不暴露）。

### 1.2 GET /v1/models
- **鉴权**：API Key
- **响应（200）**：
```json
{
  "object": "list",
  "data": [
    {"id": "deepseek-chat", "object": "model", "created": 1736900000, "owned_by": "deepseek"},
    {"id": "gpt-4o-mini", "object": "model", "created": 1736900000, "owned_by": "openai"},
    {"id": "qwen-plus", "object": "model", "created": 1736900000, "owned_by": "qwen"}
  ]
}
```
- **语义**：返回 `model_endpoint` 中 `enabled=true` 且对当前 Key scope 可见的逻辑模型去重列表。

### 1.3 POST /v1/embeddings
- **鉴权**：API Key（scope: `embeddings`）
- **请求**：`{"model": "text-embedding-3-small", "input": "文本或数组"}`
- **响应（200）**：
```json
{
  "object": "list",
  "data": [{"object": "embedding", "index": 0, "embedding": [0.01, 0.02]}],
  "model": "text-embedding-3-small",
  "usage": {"prompt_tokens": 8, "total_tokens": 8}
}
```
- **错误**：同 1.1；不支持的端点返回 `ROUTE_003`。

---

## 2. 管理面（JWT，三档角色：admin / consumer / evaluator）

### 2.1 POST /admin/v1/auth/token — 换取 JWT
- **鉴权**：API Key（scope: `admin`）
- **请求**：`{"api_key": "sk-admin-...", "ttl_minutes": 60}`
- **响应（200）**：`{"access_token": "<JWT>", "token_type": "bearer", "expires_in": 3600, "role": "admin"}`
- **JWT Claims**：`sub`(api_key_id), `tenant_id`, `role`, `exp`

### 2.2 租户管理
| 方法 | 路径 | 角色 | 说明 |
| --- | --- | --- | --- |
| POST | /admin/v1/tenants | admin | 创建租户 |
| GET | /admin/v1/tenants | admin | 列表（分页） |
| GET | /admin/v1/tenants/{id} | admin | 详情（含预算水位） |
| PATCH | /admin/v1/tenants/{id} | admin | 改预算/状态（热更新 ≤5s） |

**POST 请求/响应示例**：
```json
// 请求
{"name": "team-alpha", "monthly_budget": 5000.0}
// 201 响应
{"id": 1, "name": "team-alpha", "monthly_budget": 5000.0, "status": 1,
 "budget_used": 0.0, "created_at": "2026-01-01T00:00:00Z"}
```
- **错误**：409 CONFLICT_009（重名）/ 422 VALID_005 / 401 AUTH_001

### 2.3 项目管理（三级隔离中间层）
| 方法 | 路径 | 角色 |
| --- | --- | --- |
| POST | /admin/v1/tenants/{tid}/projects | admin |
| GET | /admin/v1/tenants/{tid}/projects | admin / consumer |
```json
// POST 请求
{"name": "search-bot", "monthly_budget": 1000.0}   // budget 可空=继承租户
// 201 响应
{"id": 7, "tenant_id": 1, "name": "search-bot", "monthly_budget": 1000.0, "status": 1}
```

### 2.4 API Key 管理
| 方法 | 路径 | 角色 | 说明 |
| --- | --- | --- | --- |
| POST | /admin/v1/projects/{pid}/api-keys | admin | 生成 Key |
| GET | /admin/v1/projects/{pid}/api-keys | admin | 列表（只显 key_prefix） |
| DELETE | /admin/v1/api-keys/{id} | admin | 吊销（软删） |
| GET | /admin/v1/api-keys/{id}/quota | admin / consumer | 配额水位 |

```json
// POST 请求
{"name": "prod-key", "scopes": ["chat"], "rpm_limit": 120, "tpm_limit": 120000, "expires_at": null}
// 201 响应（明文仅此一次！）
{"id": 3, "key": "sk-a1b2c3d4e5f6...", "key_prefix": "sk-a1b2c3", "tenant_id": 1, "project_id": 7,
 "name": "prod-key", "scopes": ["chat"], "rpm_limit": 120, "tpm_limit": 120000, "status": 1}
```
- **红线**：`key` 字段仅创建响应返回一次；列表/详情只返回 `key_prefix`；库存 `key_hash`。

### 2.5 模型端点管理
| 方法 | 路径 | 角色 |
| --- | --- | --- |
| POST | /admin/v1/endpoints | admin |
| GET | /admin/v1/endpoints | admin |
| PATCH | /admin/v1/endpoints/{id} | admin |
| DELETE | /admin/v1/endpoints/{id} | admin |
```json
// POST 请求
{"provider": "deepseek", "protocol": "openai", "model_name": "deepseek-chat",
 "base_url": "https://api.deepseek.com/v1", "credential_env": "LLM_DEEPSEEK_API_KEY",
 "weight": 1.0, "priority": 10, "canary_group": "stable",
 "cost_per_1k_prompt": 0.0002, "cost_per_1k_completion": 0.001}
// 201 响应：同请求 + {"id": 2, "enabled": true, "healthy": true, "avg_latency_ms": 0}
```
- **注意**：`credential_env` 只是环境变量**名**；接口任何时刻不接受、不返回密钥本体。

### 2.6 调用查询
- **GET /admin/v1/calls**
- **Query**：`tenant_id`、`project_id`、`model`、`status`、`source`、`trace_id`、`created_from`、`created_to`、分页参数
- **响应（200）**：
```json
{
  "total": 1523, "page": 1, "page_size": 20,
  "items": [{
    "id": 90123, "trace_id": "b3c1f0a2-...", "tenant_id": 1, "project_id": 7,
    "model": "deepseek-chat", "provider": "deepseek",
    "prompt_tokens": 120, "completion_tokens": 80, "tokens_estimated": false,
    "cost": 0.00104, "latency_ms": 830, "first_token_latency_ms": 210,
    "status": "ok", "error_code": null, "source": "api", "attempt_count": 1,
    "created_at": "2026-01-08T09:30:00Z"
  }]
}
```
- **约束**：强制 JWT 租户过滤（admin 可跨租户）；时间范围必带（默认近 7 天）→ 分区裁剪。
- **GET /admin/v1/calls/{trace_id}**：单条全链路详情（含 attempt 明细）。

### 2.7 评测任务
| 方法 | 路径 | 角色 | 说明 |
| --- | --- | --- | --- |
| POST | /admin/v1/eval-datasets | evaluator | 注册基准集（版本+checksum） |
| GET | /admin/v1/eval-datasets | evaluator | 列表 |
| POST | /admin/v1/eval-tasks | evaluator | 启动评测 |
| GET | /admin/v1/eval-tasks/{id} | evaluator | 状态/进度 |
| POST | /admin/v1/eval-tasks/{id}/cancel | evaluator | 取消 |
| GET | /admin/v1/eval-tasks/{id}/reports | evaluator | 报告列表 |
| GET | /admin/v1/eval-reports/compare | evaluator | 多模型对比 |
| GET | /admin/v1/eval-reports/{id}/export | evaluator | 导出 JSON/PNG |

```json
// POST /admin/v1/eval-tasks 请求
{"name": "q1-model-review", "dataset_id": 1,
 "models": ["deepseek-chat", "qwen-plus", "gpt-4o-mini"],
 "config": {"temperature": 0.0, "metrics": ["accuracy", "rouge_l", "latency_p95", "cost"], "concurrency": 4}}
// 202 响应
{"id": 5, "status": "pending", "progress": 0.0, "models": ["deepseek-chat", "qwen-plus", "gpt-4o-mini"]}
```
```json
// GET /admin/v1/eval-tasks/5/reports 响应
{"task_id": 5, "dataset_version": "1.2.0", "evaluator_version": "v1",
 "items": [
   {"model": "deepseek-chat", "overall_score": 0.82,
    "metrics": {"accuracy": 0.80, "rouge_l": 0.71, "bertscore": 0.88,
                "latency_p95_ms": 920, "tokens": 15230, "cost_usd": 0.0031, "tool_success_rate": 0.93}},
   {"model": "qwen-plus", "overall_score": 0.79, "metrics": {"...": "…"}}
 ]}
```
```json
// GET /admin/v1/eval-reports/compare?models=deepseek-chat,qwen-plus&dataset_version=1.2.0
{"dimensions": ["accuracy", "rouge_l", "bertscore", "latency_p95_ms", "cost_usd"],
 "series": [{"model": "deepseek-chat", "values": [0.80, 0.71, 0.88, 920, 0.0031]}, {"...": "…"}],
 "conclusion": {"winner": "deepseek-chat", "diff": {"accuracy": 0.01, "cost_usd": -0.0004}}}
```
- **错误**：404 NOT_FOUND_006 / 409 CONFLICT_009（任务执行中重复启动）/ 422 VALID_005
- **限流语义**：评测推理走 `eval:` 独立桶，不占在线配额；任务内并发受 `config.concurrency` 限制。

### 2.8 告警
| 方法 | 路径 | 角色 |
| --- | --- | --- |
| GET | /admin/v1/alerts | admin |
| PATCH | /admin/v1/alerts/{id}/resolve | admin |
```json
// GET 响应 items 项
{"id": 12, "level": "orange", "rule_code": "BUDGET_80",
 "subject_type": "tenant", "subject_id": "1", "message": "租户预算已达 82%",
 "score": 0.82, "evidence": {"budget_used": 4100.0, "budget": 5000.0},
 "resolved": false, "created_at": "2026-01-08T10:00:00Z"}
```

### 2.9 账单与成本
- **GET /admin/v1/billing/summary?tenant_id=&project_id=&month=2026-01**
```json
{"month": "2026-01", "tenant_id": 1,
 "total_cost_usd": 128.4, "total_prompt_tokens": 8200000, "total_completion_tokens": 3100000,
 "by_model": [{"model": "deepseek-chat", "cost_usd": 62.1, "tokens": 5400000}, {"...": "…"}],
 "by_project": [{"project_id": 7, "cost_usd": 40.2}, {"...": "…"}]}
```
- **GET /admin/v1/billing/export?format=csv|json**（P1 范围仅自核算导出，不对接外部计费）

### 2.10 审计日志
- **GET /admin/v1/audit-logs?request_id=&tenant_id=&actor_id=&action=&created_from=&created_to=**
```json
{"total": 42, "items": [{
  "id": 1001, "tenant_id": 1, "actor_type": "jwt_user", "actor_id": "3",
  "action": "api_key.revoke", "resource_type": "api_key", "resource_id": "3",
  "request_id": "b3c1f0a2-...", "ip": "10.0.0.5",
  "detail": {"key_prefix": "sk-a1b2c3", "reason": "rotation"},
  "created_at": "2026-01-08T11:00:00Z"}]}
```

### 2.11 配置管理（热更新）
| 方法 | 路径 | 角色 | 说明 |
| --- | --- | --- | --- |
| GET | /admin/v1/config/{namespace} | admin | namespace ∈ routing/limit/canary/alert |
| PUT | /admin/v1/config/{namespace} | admin | 全量快照提交，version+1 |
| GET | /admin/v1/config/{namespace}/revisions | admin | 变更历史 |
| POST | /admin/v1/config/{namespace}/rollback | admin | 回滚至上一版本 |
```json
// PUT /admin/v1/config/routing
{"strategy": "health_aware", "fallback_order": ["cost_first", "weighted_round_robin"],
 "circuit": {"error_rate_threshold": 0.5, "open_seconds": 30, "half_open_probes": 1}}
// 200 响应
{"namespace": "routing", "version": 7, "status": "applied", "applied_at": "2026-01-08T12:00:00Z",
 "effective_within_seconds": 5}
```
- **语义**：`applied` 后 Redis pub/sub 广播，订阅方 ≤5s 生效；回滚等价于以旧 payload 再 PUT 一次（产生新 version）。

---

## 3. 非功能接口

| 方法 | 路径 | 鉴权 | 说明 |
| --- | --- | --- | --- |
| GET | /healthz | 无 | 存活探针（进程级） |
| GET | /readyz | 无 | 就绪探针（DB/Redis 连通性） |
| GET | /metrics | 无（内网） | Prometheus 指标 |

---

## 4. 兼容性与版本策略

1. `/v1/*` 严格对齐 OpenAI Chat Completions 形状，新增字段一律 `x_` 前缀（SDK 忽略未知字段）。
2. `/admin/v1/*` 为平台自有 API，破坏性变更走 `/admin/v2` 并行版本。
3. 所有变更以 OpenAPI（FastAPI 自动生成）为准入校验，CI 校验文档与代码同步（P2-01）。
