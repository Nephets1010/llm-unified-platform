# 运维手册 — LLM Unified Platform

> 覆盖日常巡检、告警处置、灰度发布、故障演练、容量调优、密钥轮换与故障排查。

---

## 1. 日常巡检清单

| 频率 | 检查项 | 命令/入口 | 预期 |
|------|--------|-----------|------|
| 每日 | 服务健康 | `curl /healthz` + `/readyz` | 200 / ready |
| 每日 | 错误率 | Grafana → Gateway Overview → Error Rate | < 5% |
| 每日 | 预算水位 | Grafana → Cost & Budget → Budget Used Ratio | < 80% |
| 每日 | call_log 丢弃 | Grafana → Resilience → CallLog Buffer | dropped = 0 |
| 每周 | 磁盘使用率 | `df -h` | < 80% |
| 每周 | PG 连接数 | `SELECT count(*) FROM pg_stat_activity;` | < 80% max_connections |
| 每月 | 备份完整性 | 检查归档文件可读 | 可恢复 |
| 每月 | 密钥有效期 | 检查 API Key 过期时间 | 无过期未续 |

---

## 2. 告警处置 SOP（5 组）

### 2.1 HighErrorRate（错误率 > 5%，持续 2 分钟）

```bash
# 1. 查看错误码分布
curl /metrics | grep llm_errors_total

# 2. 查看最近错误日志
docker compose logs app --tail=100 | grep '"level":"error"'

# 3. 判断范围
# - 单模型错误 → 检查该模型端点健康
curl /admin/v1/endpoints -H "Authorization: Bearer $ADMIN_TOKEN"

# - 全模型错误 → 检查上游厂商状态页
# 4. 如需临时隔离故障端点
curl -X PATCH /admin/v1/endpoints/{id} -d '{"is_active": false}' -H "Authorization: Bearer $ADMIN_TOKEN"
```

### 2.2 HighLatencyP95（P95 > 5s，持续 3 分钟）

```bash
# 1. 查看延迟分布
curl /metrics | grep llm_request_latency_seconds

# 2. 判断是否某端点拖慢
# Grafana → Gateway Overview → P95 Latency（按 model 分组）

# 3. 切换到低延迟端点（临时调整路由策略）
curl -X PUT /admin/v1/config/routing \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"payload": {"strategy": "latency_first"}}'
```

### 2.3 CircuitBreakerOpen（熔断器打开）

```bash
# 1. 确认哪个端点熔断
curl /metrics | grep llm_circuit_state

# 2. 等待 30s 自动 half_open → closed
# 3. 如持续熔断，手动禁用端点
curl -X PATCH /admin/v1/endpoints/{id} -d '{"is_active": false}' -H "Authorization: Bearer $ADMIN_TOKEN"

# 4. 排查上游问题后重新启用
curl -X PATCH /admin/v1/endpoints/{id} -d '{"is_active": true}' -H "Authorization: Bearer $ADMIN_TOKEN"
```

### 2.4 BudgetExceeded80 / BudgetExceeded100

```bash
# 1. 查看预算使用
curl /admin/v1/billing/summary -H "Authorization: Bearer $ADMIN_TOKEN"

# 2. 80% 告警 → 通知租户控制用量
# 3. 100% 熔断 → 租户已被自动阻断
# - 临时提额：
curl -X PATCH /admin/v1/tenants/{id} -d '{"monthly_budget": 2000}' -H "Authorization: Bearer $ADMIN_TOKEN"
# - 或等待下月自动重置
```

### 2.5 CallLogDropped / CallLogFlushErrors

```bash
# 1. 查看丢弃/失败计数
curl /metrics | grep llm_calllog

# 2. 检查 DB 连接
curl /readyz  # database: true?

# 3. 检查 PG 磁盘空间
docker compose exec postgres df -h /var/lib/postgresql/data

# 4. 如 DB 不可用，call_log 会缓冲重试（3 次指数退避）
# 缓冲区满后丢弃（优先保主链路），恢复后手动补录
```

---

## 3. 灰度发布 SOP

### 3.1 灰度发布（≤5s 生效）

```bash
# 1. 设置灰度权重 10%（ConfigPayload 契约：{"payload": {...}}）
curl -X PUT http://localhost:8000/admin/v1/config/routing \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"payload": {"canary_weight": 0.1, "canary_endpoint_ids": [2], "canary_tenant_ids": [], "canary_key_hashes": []}}'

# 2. 观察灰度流量（Grafana → Resilience → Route Hits）
# 3. 确认无异常后逐步提高权重：0.1 → 0.3 → 0.5 → 1.0

# 租户/Key 白名单灰度（仅指定租户参与灰度）：
curl -X PUT http://localhost:8000/admin/v1/config/routing \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"payload": {"canary_weight": 0.2, "canary_endpoint_ids": [2], "canary_tenant_ids": [1, 2], "canary_key_hashes": []}}'
```

### 3.2 回滚（≤1 分钟）

```bash
# 一键回滚到上一版本
curl -X POST http://localhost:8000/admin/v1/config/routing/rollback \
  -H "Authorization: Bearer $ADMIN_TOKEN"

# 验证回滚生效
curl http://localhost:8000/admin/v1/config/routing \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

### 3.3 完整演练

```bash
bash scripts/canary_drill.sh     # 灰度发布/回滚演练
bash scripts/failover_drill.sh   # 故障切换演练
```

---

## 4. 故障演练

### 4.1 failover 演练

```bash
BASE_URL=http://localhost:8000 API_KEY=sk-your-key MODEL=gpt-4o \
  bash scripts/failover_drill.sh
```

验证点：
- 基线请求成功
- 端点故障后自动切换（≤200ms）
- 熔断器状态变化（closed → open → half_open → closed）
- `llm_failover_total` 指标递增

### 4.2 灰度演练

```bash
BASE_URL=http://localhost:8000 ADMIN_TOKEN=$ADMIN_TOKEN CANARY_WEIGHT=0.2 \
  bash scripts/canary_drill.sh
```

验证点：
- 灰度配置写入成功（返回 version + 1）
- 流量按权重分配
- 回滚后流量恢复

---

## 5. 容量与调优

### 5.1 数据库分区

call_log 按月 RANGE 分区，自动创建：

```sql
-- 查看分区
SELECT inhrelid::regclass AS partition_name
FROM pg_inherits WHERE inhparent = 'call_log'::regclass;

-- 手动创建新月分区（通常由迁移自动处理）
CREATE TABLE call_log_2026_12 PARTITION OF call_log
FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');
```

### 5.2 索引调优

```sql
-- 查看慢查询
SELECT query, calls, mean_time, total_time
FROM pg_stat_statements
ORDER BY mean_time DESC LIMIT 10;

-- 常用索引（已内置）
-- call_log: (tenant_id, created_at), (model, created_at), (status, created_at)
```

### 5.3 连接池

SQLAlchemy 默认连接池大小 10，可通过环境变量调整：

```bash
LLM_DB_POOL_SIZE=20
LLM_DB_MAX_OVERFLOW=30
```

### 5.4 Redis 内存

```bash
# 查看内存使用
docker compose exec redis redis-cli info memory

# 限流桶 key 自动过期（TTL），无需手动清理
```

---

## 6. 密钥轮换流程

### 6.1 JWT Secret 轮换

```bash
# 1. 生成新密钥
NEW_SECRET=$(openssl rand -hex 32)

# 2. 更新 .env
sed -i "s/LLM_JWT_SECRET=.*/LLM_JWT_SECRET=$NEW_SECRET/" .env

# 3. 重启 App（所有已签发 JWT 立即失效）
docker compose restart app

# 4. 通知用户重新登录换取 JWT
```

### 6.2 API Key 轮换

```bash
# 1. 创建新 Key
curl -X POST /admin/v1/api-keys \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -d '{"tenant_id": 1, "project_id": 1, "name": "new-key"}'
# 返回明文 Key（仅此一次）

# 2. 通知用户切换到新 Key

# 3. 禁用旧 Key
curl -X PATCH /admin/v1/api-keys/{id} \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -d '{"is_active": false}'
```

### 6.3 上游厂商 Key 轮换

```bash
# 1. 更新 .env 中对应变量
LLM_OPENAI_API_KEY=sk-new-key

# 2. 重启 App
docker compose restart app
```

---

## 7. 常见故障排查表

| 现象 | 错误码 | 可能原因 | 处置 |
|------|--------|----------|------|
| 401 鉴权失败 | AUTH_001 | API Key 无效/过期/被禁用 | 检查 Key 状态，重新签发 |
| 403 权限不足 | AUTH_001 | 角色不够（同鉴权类错误） | 检查 JWT role / scopes |
| 429 限流 | RATE_008 | 超出 RPM/TPM 限制 | 降低并发或申请提额 |
| 429 预算超支 | QUOTA_002 | 月度预算用尽 | 等待下月或提额 |
| 404 模型无路由 | ROUTE_003 | 无可用端点 | 检查端点配置与健康状态 |
| 404 资源不存在 | NOT_FOUND_006 | ID 错误 | 确认资源 ID |
| 502 上游错误 | UPSTREAM_004 | 厂商 API 故障 | 等待自动 failover，或禁用故障端点 |
| 504 超时 | UPSTREAM_004 | 上游响应慢 | 检查网络，调整 timeout |
| 500 内部错误 | SYS_000 | 未捕获异常 | 查看日志 `docker compose logs app` |
| 裸 500 | — | 代码 Bug | 收集 trace_id，查日志定位 |
| SSE 流式中断 | — | 反代缓冲/网络 | 确认 Nginx `proxy_buffering off` |
| 配置不生效 | CONFIG_010 | pub/sub 超时 | 等待 ≤5s，或重启 App |
| 评测 checksum 失败 | VALID_005 | 数据被篡改 | 检查 DB 数据完整性 |
| 评测任务超时 | — | 样本过多/上游慢 | 调整 task_timeout_s 或减少并发 |

---

## 8. 日志与追踪

```bash
# 查看应用日志（JSON 格式）
docker compose logs app --tail=200

# 按 trace_id 追踪全链路
docker compose logs app | grep '"trace_id":"your-trace-id"'

# 日志级别调整
LLM_LOG_LEVEL=debug LLM_DEBUG=true docker compose up -d app
```

所有日志字段包含 `trace_id`（= 响应头 `X-Request-Id`），可在调用日志、审计日志、评测日志中交叉检索。
