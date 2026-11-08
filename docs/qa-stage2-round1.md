# QA 测试报告 — 里程碑 M2 第 1 轮

| 项 | 内容 |
| --- | --- |
| 测试轮次 | 第 1 轮（共 2 轮上限） |
| 测试人 | QA 工程师 严过关（Yan） |
| 测试日期 | 2026-11-08 |
| 项目 | llm-unified-platform（大模型统一接入与能力评估中台） |
| 测试范围 | M2 里程碑 T07~T09 + M1 遗留修复（P1-01 ~ P1-06） |

---

## 1. 测试执行记录

### 1.1 环境搭建

```bash
cd D:/data/WorkBuddy/程序宇宙/repos/llm-unified-platform
.venv/Scripts/python.exe -m pip install -e ".[dev]"
# → 安装成功（Python 3.13.14）
```

### 1.2 全量测试运行（工程师交付状态）

| 轮次 | 命令 | 结果 |
| --- | --- | --- |
| 工程师基线（仅工程师测试） | `python -m pytest -v` | **146 passed, 0 failed**（与工程师声称一致） |
| 含 QA 主动测试（M2 46 个） | `python -m pytest -q` | **190 passed, 2 failed**（共 192） |

**最终测试数字：192 总数 = 190 passed / 2 failed**（2 个失败均为源码问题，见 §4）

| 测试模块 | 测试数 | 说明 |
| --- | --- | --- |
| tests/unit/test_metrics.py | 17 | 指标纯函数（工程师） |
| tests/unit/test_mcp_gateway.py | 5 | MCP 网关（工程师） |
| tests/integration/test_eval_flow.py | 16 | 评测集成（工程师） |
| tests/unit/…（M1 遗留 8 个模块） | 108 | M1 全部回归通过 |
| **tests/unit/test_qa_stage2_active.py** | **46** | **QA 主动测试（本轮新增）** |

### 1.3 评估端到端冒烟（复跑工程师命令，可复制）

```bash
cd D:/data/WorkBuddy/程序宇宙/repos/llm-unified-platform
python -c "
from llm_platform.db.session import init_db, SessionLocal
from llm_platform.eval.datasets import DatasetManager
from llm_platform.eval.metrics import compute_metrics, overall_score
from llm_platform.eval.report import ReportGenerator
init_db(); db = SessionLocal()
items = [
    {'input_prompt': '1+1=?', 'expected_output': '2'},
    {'input_prompt': '中国的首都是？', 'expected_output': '北京'},
    {'input_prompt': '水的化学式是？', 'expected_output': 'H2O'},
    {'input_prompt': '太阳系最大的行星是？', 'expected_output': '木星'},
    {'input_prompt': 'HTTP 404 表示？', 'expected_output': '未找到'},
]
mgr = DatasetManager(db)
result = mgr.register('qa-smoke', '1.0.0', items)
print(f'基准集: {result[\"item_count\"]} 条, checksum={result[\"checksum\"][:16]}...')
preds = ['2', '北京', 'H2O', '木星', '404 Not Found']
refs = [it['expected_output'] for it in items]
metrics = compute_metrics(preds, refs, latencies_ms=[100]*5, usage_list=[{'prompt_tokens':10,'completion_tokens':5}]*5, costs=[0.001]*5)
score = overall_score(metrics)
print(f'评分: {score}, metrics: {metrics}')
gen = ReportGenerator()
report = gen.generate(1, 'test-model', metrics, score, result['version'])
comparison = gen.compare([report, {**report, 'model': 'model-b', 'overall_score': score*0.8}])
print(f'对比: winner={comparison[\"conclusion\"][\"winner\"]}')
print('EVAL E2E: PASSED')
"
```

**QA 复跑输出**：
```
基准集: 5 条, checksum=513f88eefe1872e7...
评分: 0.68, metrics: {'accuracy': 0.8, 'f1': 0.8, 'rouge_l': 0.8, 'latency_p95_ms': 100, 'tokens': 75, 'cost_usd': 0.005, 'tool_success_rate': 0.0}
对比: winner=test-model
EVAL E2E: PASSED
```

**与工程师自验证输出不一致（见问题 DOC-3）**：工程师报告 checksum=14864438…、评分 0.835；同输入实测 checksum=513f88…、评分 0.68。checksum 确定性已验证（同输入两次同值），评分手算 0.35×0.8+0.25×0.8+0.25×0.8+0.15×0 = 0.68 与实测一致——**代码正确，工程师冒烟数字失实**。

---

## 2. 主动测试执行结果（B 项验证）

### B-1 指标正确性（手算核对）✅

| 测试 | 场景 | 手算值 | 结果 |
| --- | --- | --- | --- |
| test_accuracy_hand_computed | 2/4 精确匹配 | 0.5 | ✅ 一致 |
| test_f1_hand_computed | {hello,world} vs {hello,there} | F1=0.5 | ✅ 一致 |
| test_f1_partial_hand_computed | {the,cat,sat} vs {the,cat,mat} | F1=2/3 | ✅ 一致 |
| test_rouge_l_hand_computed | LCS([a,b,c],[a,c,b])=2 | F1=2/3 | ✅ 一致 |
| test_rouge_l_lcs_weighted_hand_computed | LCS=3,P=1,R=0.5 | 2/3 | ✅ 一致 |
| test_overall_score_hand_computed | 加权 0.35/0.25/0.25/0.15 | 0.68 | ✅ 一致 |
| test_overall_score_perfect_and_zero | 边界 1.0 / 0.0 | [0,1] | ✅ |
| test_overall_score_within_bounds | 7×7=49 组合扫描 | [0,1] | ✅ 无越界 |
| test_overall_score_ignores_observational_metrics | 延迟/Token/成本不参与加权 | — | ✅ |

**结论：accuracy/F1/ROUGE-L/overall_score 数值与手算完全一致，加权总分 0~1 不越界。**

### B-2 基准集防篡改 + 版本化 ✅

| 测试 | 场景 | 结果 |
| --- | --- | --- |
| test_checksum_detects_tampering | 注册后篡改字段/删样本 → 校验不通过 | ✅ 返回 False（防篡改生效） |
| test_version_fields_complete | id/name/version/item_count/checksum/source 齐全 | ✅（checksum 64 位 SHA-256） |
| test_duplicate_version_rejected | 同版本重复注册 | ✅ ConflictError CONFLICT_009 |
| test_empty_items_rejected | 空样本 | ✅ ValidationError VALID_005 |
| test_missing_input_prompt_rejected | 缺 input_prompt | ✅ ValidationError |

**结论：checksum 防篡改与版本化字段齐全。观察：校验为布尔返回（未抛异常），且评测执行流程不自动校验 checksum（手动 verify_checksum 可用）——不影响验收。**

### B-3 报告溯源 + 错误信封 ✅

| 测试 | 场景 | 结果 |
| --- | --- | --- |
| test_report_contains_traceability_fields | 报告含 dataset_version + evaluator_version | ✅（与 api-contract/data-model 一致；"dataset_source"为交付文档措辞问题，见 OBS-6） |
| test_report_persisted_with_trace_fields | eval_report 落库溯源字段 | ✅ |
| test_eval_task_missing_dataset_not_found_envelope | 缺基准集启动评分 | ✅ 404 NOT_FOUND_006 信封 |
| test_dataset_duplicate_version_conflict_envelope | 重复版本 | ✅ 409 CONFLICT_009 信封 |
| test_reports_missing_task_not_found | 任务不存在 | ✅ 404 NOT_FOUND_006 |
| test_export_missing_report_not_found | 报告不存在 | ✅ 404 NOT_FOUND_006 |

**结论：报告溯源字段齐全；缺基准集/不存在资源走 NOT_FOUND_006、冲突走 CONFLICT_009 统一信封。**

### B-4 调度状态机 ⚠️（核心通过，1 项源码 Bug）

| 测试 | 场景 | 结果 |
| --- | --- | --- |
| test_illegal_success_to_running_rejected | success→running | ✅ 拒绝（CONFLICT_009） |
| test_illegal_cancelled_to_running_rejected | cancelled→running | ✅ 拒绝 |
| test_illegal_pending_to_success_skipping_run | pending 直接跳 success | ✅ 拒绝 |
| test_failed_to_pending_retry_allowed | failed→pending 重试 | ✅ 允许 |
| test_cancel_pending_task_via_api | API 取消 pending 任务 | ✅ 200 + DB 状态 cancelled |
| test_cancel_terminal_success_rejected | 终态再取消 | ✅ 拒绝 |
| test_worker_skips_cancelled_task | worker 消费已取消任务 | ✅ 状态机拒绝，不执行（cancel 生效） |
| test_queue_retry_reenqueue_and_max | 队列重试 + MAX_RETRIES | ✅ 重新入队 / 上限停止 |
| test_request_level_timeout_exists | 请求级超时 | ✅ request_timeout_s=60s（适配器级） |
| **test_get_eval_task_returns_status** | **GET /eval-tasks/{id} 状态查询** | **❌ FAILED（源码 Bug BUG-1）** |

**超时控制（部分）**：请求级超时存在（HTTP 60s）；**调度器任务级超时控制缺失**（无 asyncio.wait_for/超时配置）→ OBS-4（归 M3/P1-11，不阻塞 M2）。

### B-5 eval:rl: 限流隔离 ✅

| 测试 | 场景 | 结果 |
| --- | --- | --- |
| test_online_exhausted_eval_still_works | 在线桶耗尽后评测仍可执行 | ✅ |
| test_eval_exhausted_online_still_works | 评测桶耗尽后在线仍可执行 | ✅ |
| test_eval_runner_uses_eval_bucket_prefix | EvalRunner 前缀 eval:rl ≠ 在线 rl | ✅ |

**结论：双向隔离生效（ADR-003）。**

### B-6 MCP 工具网关 ✅

| 测试 | 场景 | 结果 |
| --- | --- | --- |
| test_mcp_call_logged_with_source_mcp | 调用落 call_log 且 source=mcp | ✅（model=mcp:<tool>、latency、status=ok、prompt_hash） |
| test_mcp_failed_call_logged_with_error_status | 失败调用落库 | ✅ status=error、error_code=UPSTREAM_004 |
| test_mcp_unknown_tool_structured_error | 未知工具 | ✅ 结构化错误（success=False + 明确 error；注：非 errors.py HTTP 信封，见 OBS-5） |
| test_mcp_example_tools_work | echo/search 两个示例工具 | ✅ 可执行 |

### B-7 SRC-1 回归 ✅

| 测试 | 场景 | 结果 |
| --- | --- | --- |
| test_src1_create_token_empty_secret_fail_closed | 空 jwt_secret 签发 | ✅ AuthError AUTH_001（fail-closed） |
| test_src1_decode_token_empty_secret_fail_closed | 空 jwt_secret 解码 | ✅ AuthError |

### B-8 SRC-2 回归 ✅

| 测试 | 场景 | 结果 |
| --- | --- | --- |
| test_src2_stream_call_log_recorded | 流式 call_log 落库 | ✅ 含 trace_id（与 X-Request-Id 一致）、tokens_estimated=False、prompt/completion_tokens、first_token_latency_ms≥0 |
| test_src2_stream_estimated_tokens_flagged | 上游无 usage | ✅ tokens_estimated=True（估算标记正确） |

### B-9 评估端到端 ✅

| 测试 | 场景 | 结果 |
| --- | --- | --- |
| test_full_eval_flow | 5 条基准集→评分→报告→对比 | ✅（accuracy=0.8、tokens=75、cost=0.005、winner 判定、diff 归因） |
| test_compare_api_winner | 对比分析 API winner | ✅ |
| test_export_json_api | 报告 JSON 导出 | ✅ |
| 冒烟命令复跑 | 见 §1.3 | ✅ EVAL E2E: PASSED |

---

## 3. P1 评估相关条目逐条验收

| 编号 | 需求 | 验收标准 | 结论 | 证据 |
| --- | --- | --- | --- | --- |
| **P1-01** | 评测指标体系：准确率/召回/F1、语义相似度、延迟、Token 成本、工具调用成功率 | ≥5 个客观指标；指标定义文档化；结果可重算 | **PASS** | 7 个客观指标 + BERTScore 可选降级；metrics.py docstring 文档化；B-1 手算 5 项全部一致；同输入同输出波动 0% |
| **P1-02** | 自动化测试用例与基准集：固定基准集（≥200 条）+ 自定义用例上传 | 基准集版本化；同一模型重复跑分波动 ≤ 2% | **部分** | 版本化+SHA-256 防篡改 ✅；波动 0% ✅；上传 API ✅；**≥200 条固定基准集/内置种子未交付（BUG-2）** |
| **P1-03** | 评分与报告：单模型报告 + 多模型横向对比（雷达图/柱状图） | 报告可导出 JSON/PNG；支持按维度切片 | **部分** | 单模型报告+对比 ✅；JSON 导出 ✅；dimensions/series 按维度展开 ✅；PNG 实现存在（无 matplotlib 时正确降级报错），本环境未装 matplotlib 未实测出图 |
| **P1-04** | 对比分析：模型版本对比、历史趋势、A/B 结论 | 支持 ≥2 模型或 ≥2 版本对比；差异可归因到指标维度 | **PASS** | ≥2 模型 series + winner 判定 ✅；diff 按指标维度归因 ✅（历史趋势/跨任务对比为已声明 M3 遗留） |
| **P1-05** | MCP 工具网关：mcp-python-sdk 标准接入、工具注册/鉴权/调用审计 | ≥2 个示例 MCP 工具；工具调用成功率/延迟写入 call_log | **PASS** | echo+search 示例 ✅；source=mcp 落 call_log 含 latency/status ✅（sdk 接入为已声明遗留，本地代理满足验收） |
| **P1-06** | 任务调度（评测队列）：异步评测任务、并发控制、失败重试、状态机 | 评测不阻塞主链路；任务状态可查询；失败可重试 | **部分** | 异步 202+队列不阻塞 ✅；failed→pending 重试+指数退避 ✅；状态机迁移校验 ✅；**"任务状态可查询"API 崩溃（BUG-1）** |

**P1 整条通过率：3/6 PASS（P1-01/04/05），3/6 部分（P1-02/03/06），0 整条 FAIL。验收标准子项级：2 个子项 FAIL（≥200 基准集、任务状态查询）。**

---

## 4. 发现的问题清单

### 4.1 源码 Bug（需返工工程师）— 不修改源码

| # | 严重级别 | 问题 | 位置 | 失败测试 | 复现步骤 | 期望 vs 实际 |
| --- | --- | --- | --- | --- | --- | --- |
| **BUG-1** | **高** | `GET /admin/v1/eval-tasks/{id}` 端点崩溃：`EvalTaskOut.model_validate(task)` 直接传入 SQLAlchemy ORM 对象抛 pydantic ValidationError（模型未开 `from_attributes`），异常未捕获 | `src/llm_platform/api/admin/evals.py:162` | test_get_eval_task_returns_status | ① POST /admin/v1/eval-datasets 注册基准集 ② POST /admin/v1/eval-tasks 创建任务 ③ GET /admin/v1/eval-tasks/{id} | 期望 200 + `{"id","status","progress",…}`（api-contract §2.7，P1-06"任务状态可查询"）vs 实际 pydantic `ValidationError: Input should be a valid dictionary or instance of EvalTaskOut`（裸 500 级未捕获异常）。**修复建议**：`EvalTaskOut.model_config = ConfigDict(from_attributes=True)` 或显式构造 dict |
| **BUG-2** | **中** | P1-02 固定基准集（≥200 条）/内置种子未交付：`datasets.py:1` docstring 与 stage-2-report §1 交付清单均声称"内置种子"，实际无 seed 函数、无 ≥200 条种子数据文件（全仓 grep 无 seed 实现） | `src/llm_platform/eval/datasets.py:1` | test_builtin_seed_dataset_present | `grep -rn "seed" src/`；检查 data/ 与 eval 模块下 JSON | 期望 内置固定基准集（≥200 条）（PRD P1-02）vs 实际无。**修复建议（二选一）**：① 补 ≥200 条种子数据 + 加载入口；② 撤回"内置种子"声明（docstring+交付报告），并由产品确认 P1-02 交付形式 |
| **DOC-3** | **低** | 工程师自验证冒烟输出不可复现：同输入 checksum=14864438…、评分 0.835 与实测 513f88…、0.68 不符（checksum 确定性已验证、评分手算 0.68 正确——**代码无误，报告数字失实**） | `docs/stage-2-report.md:141-147` | — | 复跑 §1.3 命令 | 期望 自验证输出与代码一致 vs 实际不符（疑粘贴错误/过期输出）。**建议**：修正报告数字 |

### 4.2 观察项（非阻塞）— 严重级别：低

| # | 问题 | 位置 | 说明 |
| --- | --- | --- | --- |
| OBS-4 | 调度器任务级超时控制缺失（仅请求级 HTTP 60s 超时） | `src/llm_platform/scheduler/worker.py` | 归 P1-11（M3 异常处理与容灾）"超时控制"；P1-06 验收标准不含任务超时，不阻塞 M2。建议 M3 用 asyncio.wait_for 包裹 run_task |
| OBS-5 | MCP 无 HTTP API 端点；未知工具返回 dict 错误（success=False+error）而非 errors.py 统一信封（NOT_FOUND_006） | `src/llm_platform/eval/mcp_gateway.py:76` | gateway 层错误契约内部一致；M3 暴露 HTTP API 时需接统一错误信封 |
| OBS-6 | 交付文档措辞 dataset_source 与实现不一致（实现/api-contract/data-model 均为 dataset_version，功能正确） | `docs/stage-2-report.md:14` | 建议统一措辞 |
| OBS-7 | 评测执行流程不自动校验基准集 checksum（防篡改依赖手动 verify_checksum） | `src/llm_platform/eval/runner.py` | 建议 run_task 前自动校验（M3 增强） |

### 4.3 测试代码 Bug（QA 自修）— 无

本轮 2 个失败均为源码问题，无断言错误。说明：cancel 测试初版连带 GET 确认步骤（因 BUG-1 连带失败），已拆分为「cancel API+DB 确认」与「独立 GET 状态查询」两个测试以精确归因——属测试结构优化，GET 断言本身保留且仍失败。

---

## 5. 智能路由判定

### 判定结论：**Engineer（返工）**

| 类别 | 数量 | 处理 |
| --- | --- | --- |
| 源码 Bug | 2 个（BUG-1 高、BUG-2 中） | ❌ 需工程师修复，QA 不动源码 |
| 测试代码 Bug | 0 个 | — |
| 报告失实 | 1 个（DOC-3 低） | 建议工程师修正 stage-2-report 数字 |
| 观察项 | 4 个（低） | 不阻塞，M3 处理 |

**理由**：
1. BUG-1 使 P1-06 验收标准"任务状态可查询"直接失败，且属未捕获异常裸 500（违背 PRD"无未捕获异常"红线），必须修复
2. BUG-2 为 M2 范围 P1-02 交付缺口（交付清单声称"内置种子"但无实现），需工程师二选一处置
3. 其余全部通过：190/192，P1-01/04/05 整条 PASS，指标正确性/防篡改/隔离/SRC-1/SRC-2/端到端全部验证通过

**第 2 轮回归范围**（工程师修复后）：
- 重跑 `python -m pytest -q` 全量（预期 192/192）
- 回归验证 GET /admin/v1/eval-tasks/{id} 端点（test_get_eval_task_returns_status）
- 回归验证 test_builtin_seed_dataset_present（或按产品确认后的交付形式调整该测试）
- 抽验修复未引入回归（M1 遗留 + 流式 call_log）

---

## 6. M2 交付验收总结

| 维度 | 结果 |
| --- | --- |
| 测试总数 | 192（工程师 146 + QA 主动 46） |
| 通过率 | 98.96%（190/192） |
| P1 验收 | 3 条 PASS / 3 条部分（2 个验收子项 FAIL） |
| M1 遗留回归（SRC-1/SRC-2） | 2/2 修复验证通过 |
| 安全红线 | 无回归（M1 结论维持） |
| 智能路由判定 | **Engineer**（BUG-1 高 + BUG-2 中返工，修完做第 2 轮回归） |
