# QA 测试报告 — 里程碑 M3 第 1 轮

| 项 | 内容 |
| --- | --- |
| 测试轮次 | 第 1 轮（共 2 轮上限） |
| 测试人 | QA 工程师 严过关（Yan） |
| 测试日期 | 2026-11-08 |
| 项目 | llm-unified-platform |
| 测试范围 | M3 里程碑 T10~T12 + 7 项遗留核销（P1-07 ~ P1-11） |

---

## 1. 测试执行记录

### 1.1 全量测试（工程师交付状态）

| 命令 | 工程师声称 | QA 实测 |
| --- | --- | --- |
| `python -m pytest -q` | 212 passed | **212 passed**（QA 测试加入前基线一致） |
| 含 QA 主动测试（32 个） | — | **239 passed / 4 failed / 1 skipped**（共 244） |

**最终数字：244 = 212（工程师 198+14）+ 32（QA 主动）。4 个失败均为源码问题，1 个 skip 为本地无 promtool（已注明）。**

| 测试模块 | 测试数 | 说明 |
| --- | --- | --- |
| tests/integration/test_failover.py | 14 | 工程师 M3 新增（failover/熔断/灰度/重试） |
| tests/…（M2 基线） | 198 | 全部回归通过 |
| **tests/unit/test_qa_stage3_active.py** | **32** | **QA 主动测试（本轮新增，27 过 / 4 失败 / 1 skip）** |

### 1.2 测试 Bug 修复（QA 自修）— 1 个

| # | 问题 | 根因 | 修复 |
| --- | --- | --- | --- |
| TB-1 | test_grafana_dashboards_valid 首跑失败"无 panels" | Grafana 导出 JSON 为 `{"dashboard": {...}}` 包装结构，测试假设扁平结构 | 改为兼容两种形状（`data.get("dashboard", data)` + 行面板嵌套展开），断言本身正确 |

---

## 2. 主动测试执行结果（B 项验证）

### B-1 账单（T11）✅ 5/5

| 测试 | 场景 | 结果 |
| --- | --- | --- |
| test_summary_matches_hand_calc | 3×model-a + 2×model-b（cost 0.002/条） | ✅ 手算核对全对：0.006/0.004/总额 0.010、tokens 45/30、prompt 50/completion 25 |
| test_export_csv_parseable | csv 模块解析 | ✅ 表头 5 列 + 数据行数值一致 |
| test_export_json_serializable | json.dumps | ✅ 字段齐全 |
| test_billing_api_summary | GET /billing/summary | ✅ 200 |
| test_billing_api_export_csv | GET /billing/export?format=csv | ✅ text/csv |

### B-2 告警（T11）✅ 6/6（含 1 skip）

| 测试 | 场景 | 结果 |
| --- | --- | --- |
| test_budget_80_boundary | 0.79 不触发 / 0.80 触发 BUDGET_80 | ✅ 边界正确 |
| test_budget_100_percent_blocks | 100% 预算熔断（BudgetGuard QUOTA_002） | ✅ |
| test_error_rate_boundary | 0.049 不触发 / 0.05 触发 ERROR_RATE | ✅ 边界正确 |
| test_alert_resolve_api | PATCH /alerts/{id}/resolve | ✅ resolved=True 且列表可查 |
| test_alerts_yaml_rules | YAML 合法 + 5 组规则 | ✅ 含 HighErrorRate/HighLatencyP95/CircuitBreakerOpen/BudgetExceeded80/BudgetExceeded100/CallLogDropped/HighRateLimited |
| test_alerts_yaml_metric_names_exist | 引用指标真实存在 | ✅ 10 个 llm_* 指标全部在 metrics.py |
| test_promtool_if_available | promtool check rules | ⏭️ skip（本地无 promtool；已做 YAML 语法+语义人工核对） |

### B-3 灰度 + 配置（T12 / P1-09）✅ 7/7

| 测试 | 场景 | 结果 |
| --- | --- | --- |
| test_canary_ratio_1000 | set_canary(0.1) 后 1000 次选路 | ✅ 灰度占比在 5%~15% 统计带内（种子 42 确定性） |
| test_canary_weight_clamped | 权重 1.5/-0.5 | ✅ 钳制 [0,1] |
| test_config_pubsub_notify | 模拟 publish → 订阅回调 | ✅ |
| test_poll_versions_detects_change | 轮询兜底拉新版本 | ✅ 新版本触发 notify、无新版本不触发、再提交再触发 |
| test_config_rollback_restores_previous | 0.1→0.5→rollback | ✅ 还原 0.1 |
| test_config_api_put_get_rollback | API 全链路 | ✅ PUT→GET→rollback→GET 值还原 |
| test_config_invalid_namespace_rejected | 非法命名空间 | ✅ ConfigError |

### B-4 容灾（T12 / P1-11）✅ 3/3

| 测试 | 场景 | 结果 |
| --- | --- | --- |
| test_flush_retry_and_drop_count | flush 持续失败 | ✅ 3 次退避重试（0.01s 基数）→ 重试缓冲；缓冲 10000 满 → 丢弃计数触发 |
| test_task_timeout_triggers | task_timeout_s=0.2 + 挂起任务 | ✅ 返回 False、任务标记 failed、error 含"超时" |
| test_drill_scripts_syntax | bash -n 两脚本 | ✅ 语法通过 |

**演练脚本实跑**（起 uvicorn 本地服务后执行）：`canary_drill.sh` / `failover_drill.sh` 均**端到端执行完成、无 shell 错误**；/healthz ok、/metrics 暴露 llm_circuit_state/llm_failover_total。无真实 ADMIN_TOKEN/API_KEY 时管理步骤按脚本设计优雅跳过（实跑全路径需有效凭证，已在报告注明）。⚠️ 发现脚本请求体问题见 DOC-3-4。

### B-5 MCP HTTP API（遗留②）❌ 0/2（源码 Bug BUG-3-1）

| 测试 | 场景 | 结果 |
| --- | --- | --- |
| test_mcp_register_list_call_chain | 注册→列表→调用 | ❌ **FAILED**：POST /admin/v1/mcp/tools → **500 TypeError**（mcp.py:75 `register_tool() got an unexpected keyword argument 'description'`）；GET /tools → **500 ResponseValidationError（155 errors）**（McpToolOut 字段与 McpTool 实体不符）；POST /call → 200 但 error 为 `"object dict can't be used in 'await' expression"`（mcp.py:112 对同步 call_tool 用 await） |
| test_mcp_unknown_tool_unified_envelope | 未知工具统一信封 | ❌ **FAILED**：实际 200 + `{"success":false,"error":"object dict can't be used in..."}`，而非 404 NOT_FOUND_006 信封 |

**实测证据（独立复现脚本）**：三端点全部异常；另发现 mcp.py:82 `auth.user_id` 属性不存在（AuthContext 仅有 api_key_id），注册成功路径也会崩。整个"遗留② MCP HTTP API + 统一错误封套"实为**未接通**（无任何测试覆盖该 API，212 全绿未能发现）。**违反 P1-11 验收"无未捕获异常导致裸 500"。**

### B-6 checksum 自动校验（遗留③）❌ 0/2（源码 Bug BUG-3-2）

| 测试 | 场景 | 结果 |
| --- | --- | --- |
| test_tampered_dataset_rejected_on_run | 篡改 DB 样本后执行评分必须被拒 | ❌ **FAILED：DID NOT RAISE**——runner.py:145-163 仅 `logger.warning("eval_checksum_mismatch")` 告警后继续执行（设计注释"仅告警不阻断"），与验收"必须被拒"不符 |
| test_checksum_autoverify_consistent | 注册 checksum == get_items 重算 checksum | ❌ **FAILED**：无 metadata 键注册时形状不一致（注册 4c4f2b2c88fd vs 重算 3f2adf1a2e58）→ **自动校验对合法数据误报篡改**（datasets.py register 对原始入参算 checksum，runner 用 get_items 重建形状重算，规范化不一致） |

### B-7 Anthropic 真 SSE（遗留⑥）✅ 2/2

| 测试 | 场景 | 结果 |
| --- | --- | --- |
| test_event_stream_parsing | 构造 message_start/content_block_delta×2/message_delta 事件流 | ✅ 逐段解析 `["你","好"]`（非整段切分）、usage prompt=12/completion=5 提取正确 |
| test_event_stream_error_raises_upstream | error 事件 | ✅ UpstreamError |

### B-8 CI / Grafana（T10/T11）✅ 2/2

| 测试 | 场景 | 结果 |
| --- | --- | --- |
| test_ci_yml_structure | job/依赖/缓存/报告/门槛 | ✅ lint→test(needs:lint)、cache:pip、--junitxml + upload-artifact、gitleaks 独立 job、build-docker（main push） |
| test_grafana_dashboards_valid | 3 JSON 合法 + 指标真实 | ✅（TB-1 修复后）3 看板 panels 齐全，全部 llm_* 表达式指标存在于 metrics.py |

补充观察：build-docker 仅 `needs: test`（未依赖 gitleaks，密钥扫描失败不阻塞镜像构建）——合并门槛依赖分支保护设置（ci.yml 注释已文档化"必需 checks: lint, test, gitleaks"），标注 OBS。

### B-9 跨任务对比（遗留④）✅ 2/2

| 测试 | 场景 | 结果 |
| --- | --- | --- |
| test_compare_cross_task_trends | 2 任务×2 模型 | ✅ model_trends 聚合、best_task 判定（task2/m-a/0.9）正确 |
| test_compare_cross_task_empty | 空输入 | ✅ 空结构 |

### B-10 其他抽验

- **config rollback API 文档核对**：stage-3-report §3.4 curl 示例带 `revision_id` 请求体，实际端点无此参数（回滚固定到上一版本）——DOC-3-4。
- **Ruff**：`tests/unit/test_qa_stage3_active.py` All checks passed（QA 自测文件）。

---

## 3. P1 工程化条目逐条验收

| 编号 | 需求 | 验收标准 | 结论 | 证据 |
| --- | --- | --- | --- | --- |
| **P1-07** | CI/CD：GitHub Actions（lint/type-check/unit/integration/build/image） | PR 必须全绿方可合并；镜像自动构建并打 tag | **部分** | lint/test(+junitxml artifact)/gitleaks/Docker build(tag=sha) ✅（test_ci_yml_structure）；**缺 type-check job**（描述项）；"全绿方可合并"依赖 GitHub 分支保护（ci.yml 注释已说明） |
| **P1-08** | 可观测性：Prometheus 指标 + Grafana 看板 | 看板 ≥3 张（流量延迟/错误熔断/Token 成本）；告警覆盖 5xx、P95 延迟、预算 80% 水位 | **PASS** | 3 看板（gateway-overview/cost-budget/resilience-routing）JSON 合法且指标真实 ✅；alerts.yml 5 组规则覆盖 HighErrorRate/HighLatencyP95/BudgetExceeded80+100 ✅；metrics 新增 ROUTE_HITS/FAILOVER/BUDGET/EVAL/CALLLOG_FLUSH_ERRORS ✅；80% 边界测试通过 ✅ |
| **P1-09** | 灰度发布：按租户/Key/模型粒度权重灰度、一键回滚 | 灰度规则 5s 内生效；回滚 ≤1 分钟 | **部分** | 权重灰度（1000 次分流误差带内）✅、回滚还原 ✅、pub/sub+轮询 ≤5s 机制 ✅；**租户/Key 粒度缺失（当前端点/模型粒度）——GAP-3-3** |
| **P1-10** | 数据存储与缓存：Redis 热数据、PG 分区与索引 | 百万级 call_log 查询 P95 ≤3s；千万级 Token 计量可扩展 | **部分** | Redis 限流/健康度热数据 ✅（M1 起）；call_log 月分区 + 索引 ✅（0002 迁移）；**百万级性能 SLA 未实测**（需压测环境，标注遗留） |
| **P1-11** | 异常处理与容灾：统一错误码、重试退避、超时控制、降级兜底（Ollama） | 错误码表文档化；任一厂商故障 30s 内自动切换；无未捕获异常导致裸 500 | **部分** | 错误码表 ✅、重试退避 ✅（flush 3 次指数退避/队列退避）、超时控制 ✅（请求级 60s + 任务级 wait_for）、30s 熔断恢复切换 ✅（M1+test_failover）；**"无未捕获异常裸 500"因 MCP API FAIL（BUG-3-1）**；Ollama 显式降级兜底未见实现（failover 切换已含，描述项标注） |

**P1 工程化通过率：1/5 整条 PASS（P1-08），4/5 部分（其中 P1-11 有 1 个验收子项 FAIL）。**

---

## 4. 7 项遗留核销表

| # | 遗留项 | 声称 | QA 核销结论 | 证据 |
| --- | --- | --- | --- | --- |
| ① | 任务超时（task-level） | ✅ | **核销通过** | worker.py asyncio.wait_for(3600s 默认)；test_task_timeout_triggers ✅（超时→failed+"超时"错误） |
| ② | MCP HTTP API + 统一错误封套 | ✅ | **❌ 未核销** | 三端点全崩（TypeError/ResponseValidationError/await-dict），未知工具 200 非信封（BUG-3-1） |
| ③ | checksum 自动校验 | ✅ | **❌ 未核销** | 仅告警不拒（验收要求必须被拒）+ 规范化误报（BUG-3-2） |
| ④ | 跨任务对比 | ✅ | **核销通过** | compare_cross_task + 2 测试 ✅ |
| ⑤ | Worker 增强（超时保护） | ✅ | **核销通过** | 同①，状态机 failed 迁移 + 错误信息落库 ✅ |
| ⑥ | Anthropic 真流式 SSE | ✅ | **核销通过** | event 流逐 chunk 解析 + usage 提取 + error→UpstreamError ✅ |
| ⑦ | dataset_source 措辞 | ✅ | **核销通过** | 全仓无 dataset_source 残留（仅历史 QA 报告引用）；datasets.py 用 `source` 参数与 data-model 一致 ✅ |

**核销结果：5/7 通过，2/7 未通过（②③）。**

---

## 5. 发现的问题清单

### 5.1 源码 Bug（需返工工程师）— 不修改源码

| # | 严重级别 | 问题 | 位置 | 失败测试 | 复现/证据 | 期望 vs 实际 |
| --- | --- | --- | --- | --- | --- | --- |
| **BUG-3-1** | **高** | MCP HTTP API 全链路不可用：①`register_tool(name, description/endpoint_url/input_schema/handler)` 与 `McpGateway.register_tool(name, schema, auth_type, credential_env)` 签名不符 → TypeError 500；②`McpToolOut` 字段（description/endpoint_url/handler/is_active）与 McpTool 实体（schema_/auth_type/credential_env/enabled）不符 → ResponseValidationError 500（155 errors）；③`await gateway.call_tool(...)` 对同步方法 await → TypeError；④`auth.user_id` 属性不存在（AuthContext 仅有 api_key_id）；⑤未知工具返回 200 包裹 dict 而非 404 NOT_FOUND_006 统一信封 | `src/llm_platform/api/admin/mcp.py:75,82,97,112` + `eval/mcp_gateway.py` + `models/entities.py:256` | test_mcp_register_list_call_chain、test_mcp_unknown_tool_unified_envelope | POST /admin/v1/mcp/tools → 500；GET /tools → 500；POST /call → 200 `{"success":false,"error":"object dict can't be used in 'await' expression"}` | 期望 注册/列表/调用 200 + 未知工具 404 NOT_FOUND_006 信封（api-contract 统一信封；P1-11 无裸 500）vs 实际三端点全崩/信封缺失。**修复建议**：统一 gateway 与 API 的工具模型（entity 加字段或 DTO 对齐）、call_tool 调用去掉 await 或改 async、未知工具 raise NotFoundError、user_id→api_key_id |
| **BUG-3-2** | **中** | checksum 自动校验双缺陷：①`_verify_dataset_checksum` 不一致仅 warning 不拒绝执行（设计注释"仅告警不阻断"）；②规范化不一致——register() 对原始入参算 checksum，runner 用 get_items() 重建形状（恒带 metadata 键）重算，**无 metadata 键注册的合法数据集必然误报篡改** | `src/llm_platform/eval/runner.py:145-163`、`src/llm_platform/eval/datasets.py:23-26/36-103` | test_tampered_dataset_rejected_on_run（DID NOT RAISE）、test_checksum_autoverify_consistent（4c4f2b2c≠3f2adf1a） | 篡改 DB 样本后 run_task 正常跑完无异常；无 metadata 键注册后重算 checksum 不一致 | 期望 ①篡改后执行评分必须被拒（fail-closed，ValidationError/ConflictError）②合法注册零误报 vs 实际 ①仅告警 ②必然误报。**修复建议**：mismatch 时 raise ValidationError("checksum 不匹配")；注册时对 items 规范化（统一补齐 metadata 键）后再算 checksum |
| **GAP-3-3** | **中低** | P1-09 灰度粒度缺口：PRD 要求"按租户/Key/模型粒度的权重灰度"，实现仅端点/模型粒度（set_canary(endpoint_ids, weight)），**无租户/Key 维度过滤** | `src/llm_platform/routing/router.py:37-60` | （能力缺失，无失败测试） | _should_route_canary 仅按权重随机，无租户/Key 上下文 | 期望 租户/Key/模型粒度 vs 实际端点粒度。**处置二选一**：路由支持租户/Key 白名单灰度，或产品确认范围裁剪并改 PRD/报告措辞 |
| **DOC-3-4** | **低** | 灰度演练命令请求体与 API 契约不符：canary_drill.sh §2 与 stage-3-report §3.4 curl 发送 `{"canary_weight":...}` 扁平体，实际 ConfigPayload 要求 `{"payload": {...}}`（扁平体被解析为空 payload **静默写入空配置**）；§3.4 rollback curl 带 `revision_id` 体，端点实际无此参数（固定回滚上一版本） | `scripts/canary_drill.sh`、`docs/stage-3-report.md:98-120`、`api/admin/config.py:20-22` | （文档/脚本核对） | PUT /config/routing 扁平体 → 200 但 payload={} | 期望 照文档执行能正确写入 canary 配置 vs 实际写入空配置（误导性演练）。**修复建议**：脚本/curl 改为 `{"payload": {...}}`；rollback 示例删 revision_id |

### 5.2 观察项（非阻塞）— 严重级别：低

| # | 问题 | 说明 |
| --- | --- | --- |
| OBS-1 | ci.yml 缺 type-check job（P1-07 描述列 lint/type-check/unit/integration/build/image） | 建议补 mypy/pyright 步骤 |
| OBS-2 | build-docker 仅 needs: test，gitleaks 失败不阻塞镜像构建 | 合并门槛靠分支保护（已注释文档化）；建议 build-docker 加 `needs: [test, gitleaks]` |
| OBS-3 | P1-10 百万级 call_log 查询 P95≤3s 未实测 | 需压测环境/数据集，建议 M4 压测演练 |
| OBS-4 | P1-11 "降级兜底（Ollama）"无显式实现 | 有 ollama_adapter + failover 切换；显式降级链路未见 |
| OBS-5 | HealthRegistry 进程内单例（多实例部署需 Redis 同步） | 工程师已在 Backlog 声明（M4） |

### 5.3 测试代码 Bug（QA 自修）— 1 个

| # | 问题 | 处理 |
| --- | --- | --- |
| TB-1 | Grafana 看板 JSON 结构假设错误（`{"dashboard":{...}}` 包装） | ✅ 已修复（兼容两种形状），断言保留 |

---

## 6. 智能路由判定

### 判定结论：**Engineer（返工）**

| 类别 | 数量 | 处理 |
| --- | --- | --- |
| 源码 Bug | 2 个（BUG-3-1 高、BUG-3-2 中） | ❌ 需工程师修复，QA 不动源码 |
| 范围缺口 | 1 个（GAP-3-3 中低） | 需工程师处置（实现或产品确认裁剪） |
| 文档/脚本 Bug | 1 个（DOC-3-4 低） | 建议工程师随本轮一并修正 |
| 测试代码 Bug | 1 个（TB-1） | ✅ QA 自修落盘 |
| 遗留核销 | 5/7 通过，②③未通过 | 随 BUG-3-1/3-2 修复后第 2 轮复核 |

**理由**：
1. BUG-3-1 使遗留②整体落空且直接违反 P1-11"无未捕获异常导致裸 500"红线（MCP 三端点 500/异常返回），必须修复
2. BUG-3-2 使遗留③不满足"篡改必须被拒"验收，且存在合法数据误报缺陷，必须修复（注意两处需一起改，否则规范化不一致会把误报升级为误杀）
3. 其余大面积通过：239/244，P1-08 整条 PASS，账单/告警/灰度/配置/容灾/SSE/CI/Grafana/跨任务对比全部验证通过

**第 2 轮回归范围**（工程师修复后，2 轮上限内收口）：
- 全量 pytest（预期 244/244 全绿，promtool 视环境）
- 4 个失败测试转绿 + MCP 三端点实测 200/信封
- 篡改拒绝 + 零误报双场景复测
- DOC-3-4 脚本修复后实跑 canary_drill 验证配置写入
- GAP-3-3 按处置结论复核

---

## 7. M3 交付验收总结

| 维度 | 结果 |
| --- | --- |
| 测试总数 | 244（工程师 212 + QA 32） |
| 通过率 | 98.0%（239/244，4 源码失败 + 1 skip） |
| P1 工程化 | 1 PASS / 4 部分（P1-11 裸 500 子项 FAIL） |
| 7 项遗留核销 | 5/7 通过（②MCP、③checksum 未通过） |
| 智能路由判定 | **Engineer**（BUG-3-1 高 + BUG-3-2 中返工，修完做第 2 轮回归） |
