# QA 测试报告 — 里程碑 M2 第 2 轮（回归收口）

| 项 | 内容 |
| --- | --- |
| 测试轮次 | 第 2 轮（2 轮上限，收口轮） |
| 测试人 | QA 工程师 严过关（Yan） |
| 测试日期 | 2026-11-08 |
| 项目 | llm-unified-platform |
| 回归范围 | Round-1 约定：全量回归 + BUG-1/BUG-2 转绿 + 种子抽验 + DOC-3 数字核对 + SRC 抽验 + 端到端 |
| 前置 | docs/qa-stage2-round1.md（判定 Engineer：BUG-1 高 / BUG-2 中 / DOC-3 低） |

---

## 1. 工程师返工核实（独立验证，不复述声明）

| # | 修复声明 | 核实结果 |
| --- | --- | --- |
| BUG-1 | EvalTaskOut 加 from_attributes=True，其他 Out schema 一并检查 | ✅ `evals.py:54` `model_config = {"from_attributes": True}`；抽查 schemas.py 中 TenantOut/ProjectOut/ApiKeyOut/EndpointOut/CallLogItem 均已带 `from_attributes`（api_keys/calls/endpoints/tenants 的 model_validate(ORM) 路径安全） |
| BUG-2 | 新增 seed_data.py（200 条/5 维度）+ DatasetManager.load_seed() 幂等 | ✅ `src/llm_platform/eval/seed_data.py`（209 行）+ `datasets.py:147 load_seed`（同版本跳过）+ `test_seed_data.py`（6 测试） |
| DOC-3 | stage-2-report §4 用真实数字重写 | ✅ 已重写（含可复制命令）；数字经复跑核对全对（见 §4） |

---

## 2. 测试执行记录

### 2.1 A. 全量回归

| 命令 | 工程师声称 | QA 实测 |
| --- | --- | --- |
| `python -m pytest -q` | 198 passed | **198 passed, 0 failed, 31 warnings** ✅ 一致 |

构成核对：146（M1+M2 工程师）+ 46（QA Round-1 主动）+ 6（种子新增）= 198 ✅

### 2.2 B. Round-1 失败测试回归

| 测试 | Round-1 | Round-2 | 结果 |
| --- | --- | --- | --- |
| test_get_eval_task_returns_status | FAILED（BUG-1） | **PASSED** | ✅ 转绿 |
| test_builtin_seed_dataset_present | FAILED（BUG-2） | **PASSED** | ✅ 转绿 |

补充双路径抽验（脚本直测）：
- `GET /admin/v1/eval-tasks/999999` → **404 + NOT_FOUND_006 信封** ✅
- `GET /admin/v1/eval-tasks/{id}`（存在）→ **200 + {id, status: pending, progress: 0.0}** ✅

### 2.3 C. 种子基准集抽验

| 检查项 | 要求 | 实测 | 结果 |
| --- | --- | --- | --- |
| 条数 | ≥200 | **200** | ✅ |
| 维度数 | 5 | text_gen/reasoning/code/summarization/safety | ✅ |
| 每维度条数 | ≥30 | 各 **40** 条（40×5=200） | ✅ |
| checksum 确定性 | 两次加载同值 | 独立两次进程均为 `0681582e94486ac6…` | ✅ |
| load_seed 幂等 | 重复加载跳过 | 首次注册 id=24/item_count=200；再次返回 `skipped=True`（同 id/checksum） | ✅ |
| 样本格式 | input_prompt/expected_output 齐全 | 抽查首 3 条/中 1 条/末 2 条均含 prompt/output/metadata.dimension | ✅ |
| CLI | `python -m llm_platform.eval.seed_data --load` | 入口存在（__main__ + --load 注册路径） | ✅ |

### 2.4 D. DOC-3 数字一致性（复跑 stage-2-report §4 命令）

按报告命令原样执行（含 `rm -f llm_platform.db` 清库步骤），输出：

```
基准集: id=1, items=200, checksum=0681582e94486ac6...
维度分布: {'text_gen': 40, 'reasoning': 40, 'code': 40, 'summarization': 40, 'safety': 40}
评分: model-a overall=0.85, model-b overall=0.425
  model-a: accuracy=1.0, f1=1.0, rouge_l=1.0
  model-b: accuracy=0.5, f1=0.5, rouge_l=0.5
对比: winner=model-a, dimensions=['accuracy', 'f1', 'rouge_l', 'latency_p95_ms', 'tokens', 'cost_usd', 'tool_success_rate']
EVAL E2E SMOKE: ALL PASSED
```

**与报告 §4 声称数字逐项比对**：

| 项 | 报告声称 | QA 实测 | 一致 |
| --- | --- | --- | --- |
| checksum | 0681582e94486ac6… | 0681582e94486ac6… | ✅ |
| 维度分布 | 40×5 | 40×5 | ✅ |
| model-a overall | 0.85 | 0.85 | ✅ |
| model-b overall | 0.425 | 0.425 | ✅ |
| accuracy | 1.0 / 0.5 | 1.0 / 0.5 | ✅ |
| winner | model-a | model-a | ✅ |

**手算复核**：model-a 全对 → 0.35+0.25+0.25=0.85 ✅；model-b 半对 → ×0.5=0.425 ✅。**DOC-3 结论：数字一致、可复现、与手算相符（Round-1 失实问题已修复）。**

### 2.5 E. SRC-1/SRC-2 快速抽验

| 测试 | 结果 |
| --- | --- |
| test_src1_create_token_empty_secret_fail_closed | ✅ PASSED |
| test_src1_decode_token_empty_secret_fail_closed | ✅ PASSED |
| test_src2_stream_call_log_recorded | ✅ PASSED（trace_id/tokens_estimated/first_token_latency） |
| test_src2_stream_estimated_tokens_flagged | ✅ PASSED |

（TestM1LeftoverFixes 4/4，随全量回归通过；M1 遗留修复维持有效。）

### 2.6 F. Round-1 端到端冒烟回归

Round-1 可复制命令复跑：`基准集 5 条, checksum=513f88eefe1872e7… / 评分 0.68 / winner=test-model / EVAL E2E: PASSED` —— 与 Round-1 数字完全一致（checksum 确定性、评分手算 0.68），**无回归**。

---

## 3. 问题清单状态

| # | Round-1 问题 | 严重级别 | Round-2 状态 |
| --- | --- | --- | --- |
| BUG-1 | GET /eval-tasks/{id} 崩溃 | 高 | ✅ **已修复验证**（200/404 双路径 + 回归转绿） |
| BUG-2 | P1-02 内置种子基准集缺失 | 中 | ✅ **已修复验证**（200 条/5 维度/幂等/确定性） |
| DOC-3 | 冒烟数字失实 | 低 | ✅ **已修复验证**（复跑全对） |

### 遗留问题（不阻塞，结案备注）

| # | 问题 | 处置 |
| --- | --- | --- |
| OBS-4 | 调度器任务级超时控制缺失（仅请求级 60s） | 归 M3/P1-11（异常处理与容灾），维持观察 |
| OBS-5 | MCP 无 HTTP API；未知工具 dict 错误非统一信封 | 归 M3（暴露 API 时接 NOT_FOUND_006 信封） |
| OBS-6 | 文档 dataset_source 措辞 vs 实现 dataset_version | 文档措辞，建议 M3 文档轮统稿 |
| OBS-7 | 评测执行不自动校验基准集 checksum | 归 M3 增强（run_task 前自动校验） |
| OBS-8（新增·低） | 种子样本模板复用：reasoning/code/safety 各 40 条由 15/15/10 个模板循环拼装（metadata.idx 区分），存在重复 prompt | 质量增强建议（M3/M4 可扩充模板），条数与维度验收达标，不阻塞 |

---

## 4. 智能路由判定

### 判定结论：**NoOne（全绿收口）— M2 验收通过，可交付**

| 维度 | 结果 |
| --- | --- |
| 全量测试 | **198 passed / 0 failed**（与工程师声称一致） |
| Round-1 失败测试 | 2/2 转绿 |
| 种子抽验 | 7/7 通过（200 条/5×40 维度/checksum 确定性/幂等） |
| DOC-3 数字一致性 | **一致**（6 项数字全对 + 手算复核） |
| SRC-1/SRC-2 回归 | 4/4 通过 |
| 端到端回归 | 无回归（数字与 Round-1 一致） |
| P1-01~P1-06 | Round-1 的 3 条"部分"缺口已闭合：P1-02（种子✅）、P1-06（任务状态查询✅）；P1-03 的 PNG 仍为环境限制（实现存在+降级正确），整体验收达标 |

**理由**：Round-1 问题清单 3/3 修复并经独立验证；2 轮上限内无新增源码 Bug；5 个遗留观察项均属 M3 已声明范围或质量增强建议，不阻塞 M2。**M2 里程碑（T07~T09 + M1 遗留修复）可以"可运行成果"交付。**
