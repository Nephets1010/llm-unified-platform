# QA Stage-3 Round-2 回归报告（M3 工程化能力收口）

- **日期**：2026-11-08
- **执行人**：QA Engineer（software-qa-engineer-2）
- **项目**：llm-unified-platform（`D:/data/WorkBuddy/程序宇宙/repos/llm-unified-platform/`）
- **轮次**：M3 第 2 轮（2 轮上限内收口轮）
- **前置**：`docs/qa-stage3-round1.md`（判定 Engineer：BUG-3-1 / BUG-3-2 / GAP-3-3 / DOC-3-4）
- **验证脚本**：`qa_round2_verify.py`（21 项独立抽验，源码外独立验证）

---

## 0. 工程师修复声明核实（源码逐项检查）

| # | 修复声明 | 核实位置 | 结论 |
|---|---------|---------|------|
| ① | mcp.py 全文重写：kwargs 对齐 / McpToolOut 对齐实体 / 去 await / api_key_id / 未知工具 NotFoundError | `src/llm_platform/api/admin/mcp.py` 全文 | **属实**。`register_tool(name, schema, auth_type, credential_env)` 与 McpGateway 签名一致（handler/description 并入 schema）；McpToolOut 含 `from_attributes`，字段 id/name/schema/auth_type/credential_env/enabled 与 McpTool 实体一致；`call_tool` 同步调用无 await；日志用 `auth.api_key_id`（AuthContext 真实字段）；未知工具 `raise NotFoundError` |
| ② | checksum fail-closed raise ValidationError + normalize_items 统一规范化 | `eval/runner.py:145-172`、`eval/datasets.py:26-46` | **属实**。`_verify_dataset_checksum` mismatch 时 `raise ValidationError`（不再仅 logger.warning）；`normalize_items()` 统一补齐 input_prompt/expected_output/metadata 三键，`compute_checksum` 基于规范化形状 |
| ③ | set_canary 扩展 tenant_ids/key_hashes + `_in_canary_scope()` | `routing/router.py:41-99` | **属实**。`set_canary(endpoint_ids, weight, tenant_ids, key_hashes)`；`_in_canary_scope` 空名单=全量灰度、命中任一维度放行；`_should_route_canary` 先 scope 再权重 |
| ④ | canary_drill.sh 请求体改 `{"payload": {...}}`、rollback 去请求体 | `scripts/canary_drill.sh:28,60,66` | **属实**。PUT 体为 `{"payload": {"canary_weight":…, "canary_endpoint_ids":[2], "canary_tenant_ids":[], "canary_key_hashes":[]}}`，与 `ConfigPayload` DTO（config.py:20-21）契约一致；rollback POST 无请求体 |

---

## 1. 回归范围 A-G 执行记录

### A. 全量 pytest（真实数字）

```
243 passed, 1 skipped, 43 warnings in 88.49s
```

- **243 passed + 1 skipped = 244**，与工程师声明一致。
- skipped 为 promtool（本机无 Prometheus 工具链），跳过理由合理。
- 无 xfail / 无 error。

### B. Round-1 的 4 个失败测试转绿确认

```
4 passed in 1.27s
```

| 测试 | Round-1 | Round-2 |
|------|---------|---------|
| test_mcp_register_list_call_chain | FAIL（TypeError 五连崩） | **PASS** |
| test_mcp_unknown_tool_unified_envelope | FAIL（无 404 信封） | **PASS** |
| test_tampered_dataset_rejected_on_run | FAIL（仅告警不拒） | **PASS** |
| test_checksum_autoverify_consistent | FAIL（规范化误报） | **PASS** |

### C. MCP 三端点实测（TestClient 独立脚本，非既有测试复述）

| 项 | 操作 | 结果 |
|----|------|------|
| C1 | POST /admin/v1/mcp/tools 注册 | **200**，返回 id/name/schema/auth_type/credential_env/enabled 全字段 |
| C1b | McpToolOut 字段对齐实体 | **PASS**（serialization_alias="schema" 正常输出） |
| C2 | GET /admin/v1/mcp/tools | **200**，列表包含新注册工具 |
| C3 | POST /admin/v1/mcp/call | **200**，`{"success":true,"result":…,"error":null}` |
| C4 | 未知工具调用 | **404**，信封 `{"error":{"code":"NOT_FOUND_006","message":"MCP 工具不存在: no-such-tool-xyz",…}}` |
| C4b | 信封完整性 | trace_id / context 键均在（P1-11 统一信封达标） |

### D. checksum 双场景实测

| 项 | 场景 | 结果 |
|----|------|------|
| D1 | **无 metadata 键**数据注册 → get_items 重算 | **零误报**：reg=657e274b9d78，recompute=657e274b9d78（normalize_items 消除形状差异） |
| D1b | verify_checksum | True |
| D1c | 带 metadata 键数据 | 零误报 |
| D2 | 篡改 expected_output 后运行校验 | **必拒**：raise ValidationError「基准集 checksum 校验失败（数据可能被篡改）」——fail-closed 非告警 |
| D2b | 合法数据校验 | 通过（不误拒） |

### E. 灰度租户/Key 粒度抽验

| 项 | 场景 | 结果 |
|----|------|------|
| E1 | 空白名单（tenant_ids=[]、key_hashes=[]） | 全量灰度，兼容端点级——**PASS** |
| E2 | tenant_ids=[10,20]：命中 10 / 未命中 30 | True / False——**PASS** |
| E3 | key_hashes=[hash-a,b]：命中 a / 未命中 z | True / False——**PASS** |
| E4 | 双白名单任一维度命中即可 | 三态断言全对——**PASS** |
| E5 | 未命中白名单，weight=1.0，200 次抽样 | **0 次**进灰度（scope 先于权重生效） |
| E5b | 命中白名单，weight=1.0，200 次 | **200/200** 全进灰度 |
| E6 | clear_canary | 白名单与权重均重置——**PASS** |

### F. 两演练脚本实跑 + DOC-3-4 一致性

- `bash -n` 两脚本语法均 OK。
- **canary_drill.sh 实跑**（起 uvicorn :8177 + 真实 admin JWT）：
  - [1/6] GET config/routing → version 12 可读；
  - [2/6] PUT `{"payload":{canary_weight:0.2,…}}` → **version 13 applied**；
  - [3/6] 20 次请求统计主流量/灰度流量（脚本逻辑完整执行）；
  - [4/6] 权重 0.5 → version 14 applied；
  - [5/6] rollback（无请求体）→ version 15 applied；
  - [6/6] 回滚验证完成。**全链路 0 报错**。
- **failover_drill.sh 实跑**：
  - healthz OK；基线/10 连发请求执行；`/metrics` 暴露 `llm_failover_total`、`llm_circuit_state`（与 metrics.py 指标名一致）。**全链路 0 报错**。
- **DOC-3-4 一致性**：脚本 PUT 请求体为 `{"payload":{...}}`，与 `ConfigPayload` DTO 契约一致；额外 GET 验证 payload 内容完整落库（`canary_weight/endpoint_ids/tenant_ids/key_hashes` 四键均可读回）。

### G. Round-1 通过项抽验防回归（账单/告警/配置回滚 3 项）

| 项 | 场景 | 结果 |
|----|------|------|
| G1 | 账单手算：5 条 call_log（4×0.01+0.001=**0.041**，tokens 600+15=**615**） | summary 实测 cost=0.041 / tokens=615——**精确一致** |
| G2 | 告警边界：used_ratio=0.79 / 0.80 | 0.79→不触发，0.80→触发——**边界正确** |
| G3 | 配置回滚：put v1 → put v2 → rollback | get_current 回到 v=1——**PASS** |

---

## 2. 独立验证脚本结果汇总

`qa_round2_verify.py`（21 项断言）：**21 PASS / 0 FAIL**

> 执行过程中修正 4 处**测试脚本自身**问题（均非源码缺陷，属脚本对 API 的假设错误，QA 自修）：
> ① import 路径 `auth.keys`→`auth.api_key`；② 基准集版本号重复撞唯一约束（改 uuid 后缀）；
> ③ `check_budget_80` 签名 `spent`→`used_ratio/used`、`ConfigStore.put` 参数 `actor`→`created_by`、命名空间须在 `{routing,limit,canary,alert}` 内；
> ④ CallLog 必填 `trace_id`、账单月份过滤需匹配 `month="2025-06"`、跨轮数据污染用随机 tenant 隔离。
> 以上均验证了源码行为正确、脚本断言预期修正后即绿。

---

## 3. 判定结论

| 检查面 | 结论 |
|--------|------|
| A 全量测试 | 243+1 全绿 ✅ |
| B 4 失败测试转绿 | 4/4 ✅ |
| C MCP 三端点+404 信封 | 全部通过 ✅ |
| D checksum 双场景 | 零误报 + fail-closed 必拒 ✅ |
| E 灰度租户/Key 粒度 | 白名单命中/未命中/空名单兼容全对 ✅ |
| F 两演练脚本实跑 + DOC-3-4 | 全链路跑通，契约一致 ✅ |
| G 防回归抽验 3 项 | 账单/告警/配置回滚全对 ✅ |

**智能路由判定：NoOne（全绿）**

4 项 Round-1 缺陷（BUG-3-1 / BUG-3-2 / GAP-3-3 / DOC-3-4）全部修复并经独立实测验证。测试数字 **243 passed + 1 skipped** 真实可复现。M3 工程化能力（T10 CI/CD、T11 可观测性/账单/告警、T12 灰度/配置/容灾 + 7 项遗留收口）**可交付**。

**Known Issues**：无（2 轮内收口，无需遗留标注）。
