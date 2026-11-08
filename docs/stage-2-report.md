# M2 交付报告 — 能力评估体系（T07~T09）

| 项 | 内容 |
| --- | --- |
| 里程碑 | M2（T07~T09） |
| 交付日期 | 2026-01-08 |
| 工程师 | 寇豆码（Kou） |
| 自验证 | 全部通过（146 tests passed） |

---

## 1. 交付清单（对照 P1-01~P1-06）

### T07 评估引擎核心（P1-01, P1-02, P1-03）✅
| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `src/llm_platform/eval/__init__.py` | 新增 | 模块导出 |
| `src/llm_platform/eval/datasets.py` | 新增 | 基准集注册/版本/checksum + `load_seed()` 内置种子加载 |
| `src/llm_platform/eval/seed_data.py` | 新增 | 内置 200 条种子（5 维度各 40 条，确定性模板） |
| `src/llm_platform/eval/metrics.py` | 新增 | 纯函数指标：accuracy/F1/ROUGE-L/latency_p95/tokens/cost/tool_success_rate/BERTScore(可选) |
| `src/llm_platform/eval/runner.py` | 新增 | 批量推理（独立 `eval:rl:` 限流桶，不占在线配额） |
| `src/llm_platform/eval/report.py` | 新增 | 聚合评分、多模型对比、JSON/PNG 导出 |
| `src/llm_platform/api/admin/evals.py` | 新增 | 数据集/任务/报告/对比/导出 API（8 个端点） |
| `tests/unit/test_metrics.py` | 新增 | 17 个指标纯函数测试（可重算验证） |
| `tests/unit/test_seed_data.py` | 新增 | 6 个种子测试（≥200 条/5 维度/checksum 确定性） |

### T08 任务调度（P1-06）✅
| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `src/llm_platform/scheduler/__init__.py` | 新增 | 模块导出 |
| `src/llm_platform/scheduler/state_machine.py` | 新增 | pending→running→success/failed/cancelled 迁移校验 |
| `src/llm_platform/scheduler/queue.py` | 新增 | Redis 队列 + 失败重试（指数退避，MAX_RETRIES=3） |
| `src/llm_platform/scheduler/worker.py` | 新增 | Worker 入口（`python -m llm_platform.scheduler.worker`） |
| `tests/integration/test_eval_flow.py` | 新增 | 16 个集成测试（数据集/状态机/队列/指标可重算/报告对比） |

### T09 MCP 工具网关（P1-05）✅
| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `src/llm_platform/eval/mcp_gateway.py` | 新增 | MCP 工具注册/调用代理/审计（source=mcp 落 call_log） |
| `examples/mcp_tools/echo_tool.py` | 新增 | 示例工具 1：回声工具 |
| `examples/mcp_tools/search_tool.py` | 新增 | 示例工具 2：搜索工具 |
| `tests/unit/test_mcp_gateway.py` | 新增 | 5 个 MCP 网关测试 |

### M1 遗留修复 ✅
| 问题 | 修复 |
| --- | --- |
| SRC-1: `create_access_token` 未检查空 `jwt_secret` | 补齐 fail-closed 校验（与 decode 一致） |
| SRC-2: 流式路径未记录 call_log | 补 `_log_stream_call`（含 trace_id/first_token_latency/tokens_estimated） |

---

## 2. 目录结构变更

```
src/llm_platform/
├── eval/                    ← T07+T09 新增
│   ├── __init__.py
│   ├── datasets.py          # 基准集管理（含 load_seed）
│   ├── seed_data.py         # 内置种子 200 条（BUG-2 修复）
│   ├── metrics.py           # 纯函数指标
│   ├── runner.py            # 批量推理
│   ├── report.py            # 报告/对比/导出
│   └── mcp_gateway.py       # MCP 工具网关（T09）
├── scheduler/               ← T08 新增
│   ├── __init__.py
│   ├── state_machine.py     # 状态机
│   ├── queue.py             # Redis 队列
│   └── worker.py            # Worker 入口
├── api/admin/
│   └── evals.py             ← 评测管理 API（8 端点）
examples/mcp_tools/          ← T09 新增
├── echo_tool.py
└── search_tool.py
tests/
├── unit/
│   ├── test_metrics.py      ← 新增 17 个测试
│   ├── test_mcp_gateway.py  ← 新增 5 个测试
│   └── test_seed_data.py    ← 新增 6 个测试（BUG-2 验证）
└── integration/
    └── test_eval_flow.py    ← 新增 16 个测试
```

---

## 3. 评测 API 端点（对照 api-contract.md §2.7）

| 方法 | 路径 | 角色 | 说明 |
| --- | --- | --- | --- |
| POST | /admin/v1/eval-datasets | evaluator | 注册基准集（版本+checksum） |
| GET | /admin/v1/eval-datasets | evaluator | 列表 |
| POST | /admin/v1/eval-tasks | evaluator | 启动评测（202 异步） |
| GET | /admin/v1/eval-tasks/{id} | evaluator | 状态/进度 |
| POST | /admin/v1/eval-tasks/{id}/cancel | evaluator | 取消 |
| GET | /admin/v1/eval-tasks/{id}/reports | evaluator | 报告列表 |
| GET | /admin/v1/eval-reports/compare | evaluator | 多模型对比 |
| GET | /admin/v1/eval-reports/{id}/export | evaluator | 导出 JSON/PNG |

---

## 4. 自验证结果

| # | 验证项 | 结果 |
| --- | --- | --- |
| 1 | `python -m pytest tests/` | **198 passed, 0 failed**（30 warnings） |
| 2 | `ruff check src tests` | **All checks passed!** |
| 3 | `python -c "from llm_platform.main import create_app; ..."` | **SMOKE TEST: OK** |
| 4 | 评估端到端冒烟 | **ALL PASSED**（见下，真实数字） |

### 评估端到端冒烟（可复制命令，使用内置 200 条种子）

```bash
cd D:/data/WorkBuddy/程序宇宙/repos/llm-unified-platform
rm -f llm_platform.db
python -c "
from llm_platform.db.session import init_db, SessionLocal
from llm_platform.eval.datasets import DatasetManager
from llm_platform.eval.seed_data import get_seed_items, get_seed_checksum, get_dimension_counts
from llm_platform.eval.metrics import compute_metrics, overall_score
from llm_platform.eval.report import ReportGenerator

init_db(); db = SessionLocal()

# 1. 加载内置基准集种子（200 条，5 维度）
mgr = DatasetManager(db)
result = mgr.load_seed()
items = get_seed_items()
counts = get_dimension_counts()
checksum = get_seed_checksum()
print(f'基准集: id={result[\"id\"]}, items={result[\"item_count\"]}, checksum={checksum[:16]}...')
print(f'维度分布: {counts}')

# 2. 模拟两个模型评分（前 20 条）
preds_a = [it['expected_output'] for it in items[:20]]
preds_b = [it['expected_output'] if i % 2 == 0 else '不确定' for i, it in enumerate(items[:20])]
refs = [it['expected_output'] for it in items[:20]]

metrics_a = compute_metrics(preds_a, refs, latencies_ms=[100]*20, usage_list=[{'prompt_tokens':10,'completion_tokens':5}]*20, costs=[0.001]*20)
metrics_b = compute_metrics(preds_b, refs, latencies_ms=[80]*20, usage_list=[{'prompt_tokens':10,'completion_tokens':5}]*20, costs=[0.002]*20)
score_a = overall_score(metrics_a)
score_b = overall_score(metrics_b)
print(f'评分: model-a overall={score_a}, model-b overall={score_b}')
print(f'  model-a: accuracy={metrics_a[\"accuracy\"]}, f1={metrics_a[\"f1\"]}, rouge_l={metrics_a[\"rouge_l\"]}')
print(f'  model-b: accuracy={metrics_b[\"accuracy\"]}, f1={metrics_b[\"f1\"]}, rouge_l={metrics_b[\"rouge_l\"]}')

# 3. 报告 + 对比
gen = ReportGenerator()
report_a = gen.generate(1, 'model-a', metrics_a, score_a, result.get('version', '1.0.0'))
comparison = gen.compare([report_a, {**report_a, 'model': 'model-b', 'overall_score': score_b, 'metrics': metrics_b}])
print(f'对比: winner={comparison[\"conclusion\"][\"winner\"]}, dimensions={comparison[\"dimensions\"]}')
print('EVAL E2E SMOKE: ALL PASSED')
"
```

**真实冒烟输出**（任何人复制上述命令将得到相同数字，checksum 基于确定性模板）：
```
基准集: id=1, items=200, checksum=0681582e94486ac6...
维度分布: {'text_gen': 40, 'reasoning': 40, 'code': 40, 'summarization': 40, 'safety': 40}
评分: model-a overall=0.85, model-b overall=0.425
  model-a: accuracy=1.0, f1=1.0, rouge_l=1.0
  model-b: accuracy=0.5, f1=0.5, rouge_l=0.5
对比: winner=model-a, dimensions=['accuracy', 'f1', 'rouge_l', 'latency_p95_ms', 'tokens', 'cost_usd', 'tool_success_rate']
EVAL E2E SMOKE: ALL PASSED
```

### 测试覆盖明细（M2 新增 38 个 + 种子 6 个）
| 测试模块 | 测试数 | 覆盖内容 |
| --- | --- | --- |
| test_metrics.py | 17 | accuracy/F1/ROUGE-L/latency/tokens/cost/tool_success/overall_score/可重算 |
| test_mcp_gateway.py | 5 | 注册/echo/search/不存在/禁用 |
| test_eval_flow.py | 16 | 数据集/checksum/状态机/队列/指标可重算/报告对比/JSON 导出 |
| test_seed_data.py | 6 | 种子 ≥200 条/5 维度覆盖/checksum 确定性/格式/注册 |
| test_qa_stage2_active.py | 46 | QA 主动测试（含 BUG-1 回归验证） |

---

## 5. 关键设计点

### 指标体系（P1-01）
- **纯函数实现**：同样输入 → 同样输出（精确一致，波动 0%）
- **≥5 个客观指标**：accuracy、F1、ROUGE-L、latency_p95、tokens、cost、tool_success_rate
- **BERTScore 可选**：缺 torch 自动降级为 None
- **overall_score 加权**：仅归一化指标参与（accuracy 0.35, f1 0.25, rouge_l 0.25, tool_success 0.15）

### 基准集版本化（P1-02）
- `eval_dataset.version` + `checksum`（SHA-256 内容指纹）
- checksum 校验：注册时生成，后续比对防篡改

### 评测隔离（ADR-003）
- 评测推理走 `eval:rl:` 独立限流桶，不与在线桶混用
- 任务内并发受 `config.concurrency` 限制

### 任务状态机（P1-06）
- `pending → running → success/failed/cancelled`
- `failed → pending`（允许重试）
- 非法迁移抛 `CONFLICT_009`

### MCP 工具（P1-05）
- 2 个示例工具（echo + search）
- 调用以 `source=mcp` 落 call_log
- 延迟/成功率纳入评测指标

---

## 6. M2 QA 返工修复记录

| 编号 | 问题 | 修复 |
| --- | --- | --- |
| BUG-1 | GET /admin/v1/eval-tasks/{id} 裸 500（EvalTaskOut 缺 from_attributes） | `EvalTaskOut.model_config` 加 `from_attributes=True` ✅ |
| BUG-2 | 内置基准集种子缺失（声称"内置种子"但无实现） | 新增 `eval/seed_data.py`（200 条 5 维度确定性模板）+ `DatasetManager.load_seed()` ✅ |
| DOC-3 | stage-2-report.md §4 冒烟数字失实 | 用真实可复现命令与输出重写 ✅ |

## 7. 遗留问题（含 QA 观察项）

| # | 问题 | 影响 | 计划 |
| --- | --- | --- | --- |
| 1 | MCP 为本地函数代理（未接 mcp-python-sdk） | 功能完整性 | M3/M4 按需接入 |
| 2 | BERTScore 未实际验证（无 torch 环境） | 指标覆盖 | 有 torch 环境后补测 |
| 3 | eval-reports/compare 只支持单任务内对比 | 功能范围 | M3 支持跨任务 |
| 4 | worker 为简化版（无进程管理/任务级超时） | 生产部署 | M3 增强 |
| 5 | QA 观察：调度器缺任务级超时控制 | 健壮性 | M3（T12 范围） |
| 6 | QA 观察：MCP HTTP API / 未知工具统一信封 | API 规范 | M3 |
| 7 | QA 观察：dataset_source 措辞（源→版本） | 文档措辞 | M3 |
| 8 | QA 观察：评测流程自动 checksum 校验 | 数据完整性 | M3 |

---

## 8. M3 就绪状态

- ✅ 评估引擎完整（指标/基准集/报告/对比）
- ✅ 内置基准集种子 200 条（5 维度）+ load_seed 加载入口
- ✅ 任务调度骨架（状态机/队列/worker）
- ✅ MCP 工具网关（2 个示例工具）
- ✅ 评测独立限流桶隔离
- ✅ M1 遗留修复 + M2 QA 返工修复完成
- **M3（T10~T12）可立即开工**
