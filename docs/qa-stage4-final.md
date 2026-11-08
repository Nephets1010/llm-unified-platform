# QA Stage-4 最终验收报告（M4 收官轮）

- **日期**：2026-11-08
- **执行人**：QA Engineer（software-qa-engineer-2）
- **项目**：llm-unified-platform（`D:/data/WorkBuddy/程序宇宙/repos/llm-unified-platform/`）
- **验收目标**：确认"从零按 README 能跑通"、文档与实现一致、可公开发布
- **验证方式**：新用户视角实测（不看其他文档只按 README 操作）+ 逐项独立核对

---

## 0. 工程师声明核实（声明 vs 实测）

| # | 声明 | 实测 | 结论 |
|---|------|------|------|
| 1 | README.md（架构图/快速上手/配置表/API 摘要/文档索引 15 篇） | 文件齐备；mermaid 8 subgraph/8 end 配对、语法合法；文档索引 15 篇全存在；配置表 16 键 | ✅ 部分属实（配置表 1 键默认值失实、快速上手 2 处命令错，见问题清单） |
| 2 | docs/deployment.md | 存在；迁移命令实跑通过（0001→0002）；但 docker compose 命令路径错 | ⚠️ 见 BUG-M4-3 |
| 3 | docs/operations.md | 存在；5 组告警 SOP 与 alerts.yml 规则名全核对一致；但 §7 错误码表 4 处失实 | ⚠️ 见 DOC-M4-5 |
| 4 | scripts/demo_quickstart.sh（7 步演示） | 7 步流程结构完整、bash -n 通过；**第 3 步 seed 实跑崩溃** | ❌ 见 BUG-M4-2 |
| 5 | CONTRIBUTING.md / CHANGELOG.md（v0.1.0） / docs/stage-4-report.md | 均存在，内容与项目一致 | ✅ |
| 6 | 243 passed + 1 skipped | 实测 243 passed, 1 skipped in 87.67s | ✅ 数字真实 |
| 7 | 文档互链 20/20 | 17 个 README 链接目标 + 3 个脚本全存在 | ✅ |
| 8 | git 8 commits + annotated tag v0.1.0；未 push | `git log` 8 commits、`git cat-file -t v0.1.0`=tag（注解标签）、无 remote（未 push） | ✅ |
| 9 | （stage-4-report §3.2）Ruff 零 error（exit code 0） | 实测 `ruff check src/ tests/` **exit 1，5 处违规** | ❌ 声明失实，见 BUG-M4-4 |

---

## 1. A. 新用户视角快速上手实测（核心）

**环境断点声明**：本机无 Docker（`docker: command not found`），README"方式一 Docker Compose"无法实跑，执行方式二 venv 路径 + 可离线部分，断点逐项注明。

### 1.1 逐命令执行记录

| 步骤 | README 命令 | 实测结果 |
|------|-------------|---------|
| venv 创建 | `python -m venv .venv` | 复用既有 venv（Python 3.13.14）；`pip install -e ".[dev]"` 重跑 **exit 0** ✅ |
| 全量测试 | `python -m pytest tests/ -q` | **243 passed, 1 skipped**（promtool skip）in 87.67s ✅ 与"243+ 用例"一致 |
| 迁移 | `alembic upgrade head` | **有 alembic.ini 时** 0001→0002 执行成功 ✅；**但 alembic.ini 未入 git，新 clone 必失败** ❌（BUG-M4-1，实测移走 alembic.ini 后报 `FAILED: No 'script_location' key found`） |
| 一键演示 | `bash scripts/demo_quickstart.sh` | 脚本语法 OK，7 步实跑记录见下表；**第 3 步实跑崩溃** ❌（BUG-M4-2） |
| Docker 验证 | `docker compose up -d` / `docker compose config -q` | **断点①**：本机无 Docker；且命令本身路径错（BUG-M4-3）：compose 文件在 `deploy/docker-compose.yml`，仓库根执行必报 "no configuration file provided" |

### 1.2 demo_quickstart.sh 七步实跑（起本地 uvicorn :8000 + SQLite 离线等价环境）

| 步 | 声称 | 实测 | 判定 |
|----|------|------|------|
| 1/7 | 检查服务健康 | `/healthz: {"status":"ok"}` | ✅ |
| 2/7 | 检查就绪 | `{"status":"ready","checks":{"database":true,"redis":false}}`（本机无 Redis，降级） | ✅（断点②：无 Redis） |
| 3/7 | 创建租户/Key/端点 | **崩溃**：`TypeError: 'name' is an invalid keyword argument for ModelEndpoint`（实体无 name 字段）；连锁 demo_api_key.txt 未生成 | ❌ BUG-M4-2 |
| 4/7 | 加载 200 条基准集 | ✅ v1.0.0 200 条，checksum `0681582e94486ac6...`（与 M2 验收记录一致） | ✅ |
| 5/7 | 发起 chat 请求 | 未成功，脚本优雅降级提示"需要配置真实 API Key" | ⚠️ 断点③：无真实厂商 API Key（脚本降级行为符合预期，但因第 3 步崩溃本应有效的演示 Key 也不存在） |
| 6/7 | 运行评测（模拟指标） | accuracy=0.60（手算核对：5 条中 3 条精确匹配=0.60 ✓）；综合评分 0.5100（手算 0.35×0.6+0.25×0.6+0.25×0.6+0.15×0=**0.51** ✓） | ✅ |
| 7/7 | 导出账单 | ✅ 0 行（无调用），CSV 表头正确 | ✅ |

**结论**：7 步中 6 步可离线跑通（其中 2 步为环境断点、降级合理），**第 3 步为真实脚本 Bug**。"从零按 README 跑通"目标 **未达成**。

---

## 2. B. 文档准确性抽查

### 2.1 deployment.md 迁移命令实跑（SQLite 等价验证）

```
alembic current        → (空，初始)
alembic upgrade head   → Running upgrade → 0001_initial → 0002_platform_v2 ✅
alembic current        → 0002_platform_v2 (head) ✅
alembic downgrade -1   → 回退 0002 → 0001 ✅
alembic history        → 0001_initial → 0002_platform_v2 (head) ✅
```

"迁移执行顺序 0001 → 0002（必须按顺序）"与 history 输出一致 ✅。

### 2.2 operations.md 告警 SOP 与 alerts.yml 规则名核对（超出"任选 2 条"，全 7 个名核对）

| SOP | alerts.yml 规则名 | 一致 |
|-----|------------------|------|
| 2.1 HighErrorRate | HighErrorRate | ✅ |
| 2.2 HighLatencyP95 | HighLatencyP95 | ✅ |
| 2.3 CircuitBreakerOpen | CircuitBreakerOpen | ✅ |
| 2.4 BudgetExceeded80 / BudgetExceeded100 | 同名 | ✅ |
| 2.5 CallLogDropped / CallLogFlushErrors | 同名 | ✅ |

### 2.3 README 配置表 16 键与 .env.example 逐一比对

16 键**名字全部一致**（LLM_ENVIRONMENT/DATABASE_URL/REDIS_URL/JWT_SECRET/JWT_EXPIRE_MINUTES/OPENAI_API_KEY/DEEPSEEK_API_KEY/QWEN_API_KEY/ANTHROPIC_API_KEY/OLLAMA_BASE_URL/REQUEST_TIMEOUT_S/RATE_LIMIT_DEFAULT_RPM/RATE_LIMIT_DEFAULT_TPM/DEFAULT_MONTHLY_BUDGET/BUDGET_ALERT_RATIO/ROUTING_STRATEGY），默认值 15/16 一致；**1 处失实**：`LLM_DATABASE_URL` 表中写默认值 `postgresql+psycopg://...`，config.py 实际默认 `sqlite:///./llm_platform.db`（DOC-M4-6）。
.env.example 另有 16 个键未进摘要表（LLM_DEBUG/MAX_RETRIES/CIRCUIT_*/RISK_* 等）——README 明示"完整列表见 .env.example"，表为子集，**不计问题**。全部 32 个 env 键均映射 config.py 真实字段（32/32 OK）。

---

## 3. C. 文档互链与 mermaid

- README 相对链接目标 **17/17 全存在**（.env.example、LICENSE、docs/×15）；stage-4-report 声明的 20 = 17 + scripts/×3（demo_quickstart.sh、canary_drill.sh、failover_drill.sh），**20/20 全存在** ✅。
- mermaid 架构图人工语法核对：`flowchart TB` 合法；8 组 `subgraph...end` 配对；节点形状（`[..]`、`[..("...")]`）合法；边 `-->`/`-.->`/`&` 多目标语法合法；所有边引用节点均有定义（24 个节点逐一核对）；`<br/>` 标签合法。**可渲染** ✅。

---

## 4. D. 发布安全

| 检查项 | 方法 | 结果 |
|--------|------|------|
| git status 干净 | `git status --porcelain` | ❌ **不干净**：`?? alembic.ini`（工程必需文件未入库，BUG-M4-1）、`?? data/`（孤儿数据未入库/未 ignore）、`?? llm_platform.db`/`llm_platform_test.db`（运行产物，.gitignore 缺 `*.db`）、`?? qa_round2_verify.py`（**QA 自有产物**，M3-R2 验证脚本，建议随报告归档或 ignore） |
| git log 8 commits | `git log --oneline` | ✅ 恰 8 条，与声明一致（stage-4-report §4 列 6 批描述性条目与实际 8 commit 消息不完全对应，属报告描述性偏差，不计阻塞） |
| tag v0.1.0 | `git tag -n` + `git cat-file -t` | ✅ 存在且为 annotated tag（type=tag） |
| 未 push | `git remote -v` | ✅ 无 remote，不可能已 push |
| 全历史密钥扫描 | `git log -p --all` 扫描 sk-/api_key=/BEGIN RSA|OPENSSH|EC|PGP/AKIA/ghp_/xox 模式 | ✅ **无真实密钥**；仅命中 mask_secret 测试样例 `sk-abcdef...`（测试占位符）与 .env.example `sk-admin-demo-key-change-me`（显式 change-me 占位） |
| .env 不在版本库 | `git ls-files` | ✅ 未跟踪且 .gitignore 含 `.env` |
| LICENSE | 文件内容 | ✅ MIT |

---

## 5. E. 全量 pytest + ruff 终跑

| 命令 | 结果 |
|------|------|
| `python -m pytest tests/ -q` | **243 passed, 1 skipped**（promtool，本地无此工具，skip 理由合理），0 failed |
| `ruff check src/ tests/ --quiet` | **exit 1，5 处违规**：UP041（watcher.py:78 `asyncio.TimeoutError`→`TimeoutError`）、I001（runner.py:151 import 未排序）、UP017×2（alerts.py:89 / billing.py:33 `timezone.utc`→`UTC`）、F841（billing.py:46 未用变量 `month_pattern`）。stage-4-report"Ruff 零 error"**失实** |

---

## 6. 问题清单（智能路由）

| # | 级别 | 类型 | 位置 | 复现/证据 | 期望 vs 实际 | 路由 |
|---|------|------|------|-----------|--------------|------|
| BUG-M4-1 | **高（发布阻塞）** | 源码/仓库 | `alembic.ini` 未跟踪（`git ls-files alembic.ini` 空） | 移走 alembic.ini 后 `alembic upgrade head` 报 `FAILED: No 'script_location' key found` | 新 clone 按 README/deployment.md 执行迁移应成功 vs 必失败 | **Engineer**：`git add alembic.ini` |
| BUG-M4-2 | **高（演示阻塞）** | 源码（脚本） | `scripts/demo_quickstart.sh:56-61` | 实跑第 3 步：`TypeError: 'name' is an invalid keyword argument for ModelEndpoint`（entities.py:87-105 实体无 name 字段） | 7 步 seed 应成功 vs 崩溃（连带 demo_api_key.txt 不生成） | **Engineer**：删除 `name=` 参数（或对齐实体字段） |
| BUG-M4-3 | **高（README 命令错）** | 文档（结构性） | README 快速上手方式一/方式二、测试段；deployment.md §1.2/§2.2/§2.3/§5 | docker compose/docker build 在仓库根执行，但 compose 文件在 `deploy/docker-compose.yml`、Dockerfile 在 `deploy/Dockerfile` | `docker compose up -d` 应可用 vs "no configuration file provided"（静态确定，CLI 行为） | **Engineer**：命令补 `-f deploy/docker-compose.yml`（build 补 `-f deploy/Dockerfile`）或指示 `cd deploy` |
| BUG-M4-4 | **中** | 源码 + 报告失实 | ruff 5 处（watcher.py:78、runner.py:151、alerts.py:89、billing.py:33/46）；stage-4-report §3.2 | 实测 exit 1 vs 声明 exit 0 | README 让新用户跑 `ruff check` 应零 error vs 报 5 处 | **Engineer**：修 5 处 lint（多为 `--fix` 可自动修），改正报告数字 |
| DOC-M4-5 | **中** | 文档（事实性） | `docs/operations.md:273-278` §7 故障排查表 | 错误码 `AUTH_002`/`RATELIMIT_001`/`ROUTE_001`/`UPSTREAM_001` 在 errors.py 中**均不存在**（真实码：AUTH_001 / RATE_008 / ROUTE_003 / UPSTREAM_004） | 运维按码检索应命中 vs 检索不到 | **Engineer**：改正 4 处错误码（403 权限场景 errors.py 尚无对应码，需同步决策） |
| DOC-M4-6 | 低 | 文档（事实性） | README 配置表 `LLM_DATABASE_URL` 行 | 表称默认值 `postgresql+psycopg://...`；config.py:30 默认 `sqlite:///./llm_platform.db` | 默认值应与代码一致 | **Engineer**（低风险文案，一句话修正） |
| DOC-M4-7 | 低 | 文档（表述） | README:144 "一键演示…自动完成：**起服务**→seed→…" | demo 脚本不起服务，仅检查 /healthz 且服务未运行时 exit 1 | 应写"需先启动服务" | **Engineer**（低风险文案） |
| OBS-M4-8 | 观察（不阻塞） | 行为存疑 | `/readyz` | redis:false 时仍返回 `status:"ready"` | 若 Redis 属就绪条件，status 语义存疑；若可选降级则合理 | 留工程师确认语义 |
| NOTE-M4-9 | 备注 | 环境 | demo 脚本用 `python3` | 本机 Git Bash 下解析到全局解释器（依赖可用，实跑通过）；Linux/macOS venv 内正常 | — | 建议脚本内改用 `python`/`$VIRTUAL_ENV`，非阻塞 |

**QA 自留产物说明**：`qa_round2_verify.py`（M3-R2 验证脚本）为 QA 产出、非工程师遗漏；`llm_platform*.db` 为测试运行产物。建议工程师在 .gitignore 补 `*.db` 并决定 `data/` 去向，QA 产物随报告归档。

---

## 7. 最终判定

# 判定：**需修复**（fix-then-publish）

**理由**："从零按 README 能跑通"三条主路径各有阻塞——
1. 方式一（Docker）：README compose 命令路径错误（BUG-M4-3）；
2. 方式二（venv）：新 clone 缺 alembic.ini，迁移必败（BUG-M4-1）；
3. 一键演示：第 3 步 seed 崩溃（BUG-M4-2）。

另有 ruff 5 处违规且报告声明失实（BUG-M4-4）、运维手册错误码失实（DOC-M4-5）。

**修复后可发布**：以上均为小范围修复（提交 1 个文件、脚本删 1 个参数、文档补路径前缀、`ruff --fix` + 改 4 个错误码），无架构级问题。测试基线健康（243 passed + 1 skipped），密钥安全检查通过（全历史无真实密钥、.env 未入库、LICENSE/CHANGELOG/CONTRIBUTING 齐备、git 历史规范）。
