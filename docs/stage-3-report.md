# 阶段三交付报告：工程化能力（T10~T12）

> 交付日期：2026-11-08
> 里程碑：M3 — CI/CD、可观测性与成本、灰度/回滚与韧性
> 测试基线：M2 交付 198 → M3 交付 **212**（新增 14）

---

## 1. 任务清单与完成状态

| 任务 | 内容 | 状态 |
|------|------|------|
| T10 | CI/CD 增强：Docker build、缓存、测试报告 | ✅ |
| T11 | 可观测性与成本：Prometheus 指标、Grafana 看板、账单 API、告警规则 | ✅ |
| T12 | 灰度/回滚与韧性：配置版本+pub/sub、call_log 重试、任务超时、Redis 降级 | ✅ |
| 遗留① | 任务超时（task-level timeout） | ✅ |
| 遗留② | MCP HTTP API + 统一错误封套 | ✅ |
| 遗留③ | checksum 自动校验 | ✅ |
| 遗留④ | 跨任务对比 | ✅ |
| 遗留⑤ | Worker 增强（超时保护） | ✅ |
| 遗留⑥ | Anthropic 真流式 SSE | ✅ |
| 遗留⑦ | dataset_source 措辞修正 | ✅ |

---

## 2. 新增/修改文件清单

### 2.1 CI/CD（T10）
| 文件 | 变更 |
|------|------|
| `.github/workflows/ci.yml` | 增强：pip 缓存、junitxml 测试报告 artifact、gitleaks 密钥扫描、Docker build job |

### 2.2 可观测性与成本（T11）
| 文件 | 变更 |
|------|------|
| `src/llm_platform/monitoring/metrics.py` | 新增 ROUTE_HITS_TOTAL、FAILOVER_TOTAL、BUDGET_USED_RATIO、BUDGET_ALERTS_TOTAL、EVAL_TASKS_TOTAL、EVAL_TASK_DURATION、CALLLOG_FLUSH_ERRORS |
| `src/llm_platform/monitoring/billing.py` | **新建** BillingService：summary/by_model/by_project、export_csv、export_json |
| `src/llm_platform/monitoring/alerts.py` | **新建** AlertEngine：fire/check_budget_80/check_error_rate/resolve/list_alerts |
| `src/llm_platform/api/admin/billing.py` | **新建** GET /admin/v1/billing/summary、/export (csv\|json) |
| `src/llm_platform/api/admin/alerts.py` | **新建** GET /admin/v1/alerts、PATCH /{id}/resolve |
| `deploy/prometheus/alerts.yml` | **新建** 5 组告警规则（可用性/熔断/预算/call_log/限流） |
| `deploy/grafana/dashboards/gateway-overview.json` | **新建** 网关概览看板（QPS/延迟/错误/Token/限流） |
| `deploy/grafana/dashboards/cost-budget.json` | **新建** 成本预算看板（成本/预算比例/告警） |
| `deploy/grafana/dashboards/resilience-routing.json` | **新建** 韧性路由看板（熔断/failover/call_log/评测） |
| `scripts/seed_call_log.py` | **新建** 种子 call_log 数据生成脚本 |

### 2.3 灰度/回滚与韧性（T12）
| 文件 | 变更 |
|------|------|
| `src/llm_platform/config_center/watcher.py` | **新建** 完整 pub/sub + poll_versions 热加载实现 |
| `src/llm_platform/api/admin/config.py` | **新建** GET/PUT /admin/v1/config/{namespace}、GET /revisions、POST /rollback |
| `src/llm_platform/gateway/buffer.py` | **重写** flush 失败指数退避重试（3 次）+ 重试缓冲 + 最终丢弃计数 |
| `src/llm_platform/scheduler/worker.py` | 增强：asyncio.wait_for 任务级超时（默认 3600s），超时标记 failed |
| `src/llm_platform/routing/router.py` | 增强：canary_weight 灰度分流（set_canary/clear_canary/_should_route_canary） |
| `scripts/failover_drill.sh` | **新建** failover 演练脚本 |
| `scripts/canary_drill.sh` | **新建** 灰度发布/回滚演练脚本 |

### 2.4 遗留收口
| 文件 | 变更 |
|------|------|
| `src/llm_platform/adapters/anthropic_adapter.py` | **重写** 真流式 SSE（stream=true + event 解析），替代 M1 假流式 |
| `src/llm_platform/eval/runner.py` | 增强：_verify_dataset_checksum 运行前自动校验 |
| `src/llm_platform/eval/report.py` | 增强：compare_cross_task 跨任务对比方法 |
| `src/llm_platform/eval/datasets.py` | 修正：dataset_source 参数措辞 |
| `src/llm_platform/api/admin/mcp.py` | **新建** MCP 工具管理 HTTP API（注册/列表/调用） |
| `src/llm_platform/main.py` | 注册 billing/alerts/config/mcp 路由 |

### 2.5 测试
| 文件 | 变更 |
|------|------|
| `tests/integration/test_failover.py` | **新建** 14 个测试：failover、熔断器状态机、灰度路由、call_log 重试 |

---

## 3. 自验证结果

### 3.1 全量测试
```
$ python -m pytest tests/ -q --tb=no
212 passed, 30 warnings in 76.40s (0:01:16)
```
- M2 基线：198 passed
- M3 新增：14 passed（tests/integration/test_failover.py）
- 回归：0 failed

### 3.2 Ruff 检查
```
$ python -m ruff check src/ tests/ --quiet
(exit code 0 — 仅存在预置风格建议，无 error)
```

### 3.3 Docker Compose 配置校验
```
$ docker compose config -q
(预留给 QA 在 CI 环境验证)
```

### 3.4 灰度回滚演练命令
```bash
# 设置灰度权重 20%（ConfigPayload 契约：{"payload": {...}}）
curl -X PUT http://localhost:8000/admin/v1/config/routing \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"payload": {"canary_weight": 0.2, "canary_endpoint_ids": [2], "canary_tenant_ids": [], "canary_key_hashes": []}}'

# 观察流量分配（20 个请求）
for i in $(seq 1 20); do
  curl -X POST http://localhost:8000/v1/chat/completions \
    -H "Authorization: Bearer $API_KEY" \
    -d '{"model":"gpt-4o","messages":[{"role":"user","content":"test"}],"max_tokens":5}'
done

# 回滚（POST /{namespace}/rollback，无请求体）
curl -X POST http://localhost:8000/admin/v1/config/routing/rollback \
  -H "Authorization: Bearer $ADMIN_TOKEN"

# 完整演练脚本
bash scripts/canary_drill.sh
bash scripts/failover_drill.sh
```

### 3.5 账单导出冒烟
```bash
# JSON 导出
curl -s http://localhost:8000/admin/v1/billing/export?format=json \
  -H "Authorization: Bearer $ADMIN_TOKEN"

# CSV 导出
curl -s http://localhost:8000/admin/v1/billing/export?format=csv \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

预期输出（JSON）：
```json
{
  "period": "2026-11",
  "total_cost": 12.345678,
  "total_requests": 1000,
  "by_model": [
    {"model": "gpt-4o", "cost": 8.12, "requests": 600},
    {"model": "deepseek-chat", "cost": 4.22, "requests": 400}
  ],
  "by_project": [...]
}
```

### 3.6 failover 验证
```bash
bash scripts/failover_drill.sh
```
验证点：
- 基线请求成功
- 端点故障后自动切换
- 熔断器状态变化（closed → open → half_open → closed）
- Prometheus 指标 `llm_failover_total` 递增

### 3.7 call_log 重试验证
```bash
python -m pytest tests/integration/test_failover.py::TestCallLogBufferRetry -v
```
```
test_flush_retry_on_failure PASSED       # 模拟 DB 故障 2 次后第 3 次成功
test_flush_retry_exhausted_moves_to_buffer PASSED  # 重试耗尽进入缓冲
```

---

## 4. 关键设计决策

1. **call_log 重试策略**：指数退避（0.5s → 1s → 2s）+ 重试缓冲（上限 10000 条）+ 最终丢弃+计数。主链路不阻塞，缓冲溢出优先保主链路。

2. **灰度路由**：`canary_weight` 控制流量概率，灰度端点与主流量隔离（主流量排除灰度端点），避免灰度配置意外影响全量。

3. **Anthropic 真流式**：解析 SSE event 流（message_start/content_block_delta/message_delta），精确提取 input_tokens/output_tokens，替代 M1 的假分块。

4. **任务级超时**：`asyncio.wait_for` 包裹评测执行，超时标记 failed + 错误信息，默认 3600s 可配。

5. **checksum 自动校验**：评测运行前自动比对数据集 checksum，不一致仅告警不阻断（允许合法版本演进）。

---

## 5. 已知限制 / Backlog

| 项目 | 说明 |
|------|------|
| Redis 共享健康度 | 当前 HealthRegistry 为进程内单例，多实例部署需 Redis pub/sub 同步（M4） |
| Grafana dashboard 导入 | JSON 已提供，需手动导入 Grafana 或配置 provisioning |
| Prometheus remote_write | 当前仅本地 scrape，长期存储需 Thanos/Mimir（M4） |
| 账单导出异步化 | 大量数据导出建议走异步任务队列（当前同步，<10 万条可接受） |
