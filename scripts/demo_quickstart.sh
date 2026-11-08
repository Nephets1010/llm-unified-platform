#!/usr/bin/env bash
# 一键演示脚本：起服务 → seed 租户/Key/端点 → 加载基准集 → 发 chat 请求 → 跑评测 → 导出账单
# 用法：bash scripts/demo_quickstart.sh
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
echo "═══════════════════════════════════════════"
echo "  LLM Unified Platform — 一键演示"
echo "  目标: ${BASE_URL}"
echo "═══════════════════════════════════════════"

# ── 1. 检查服务健康 ──
echo ""
echo "[1/7] 检查服务健康..."
HEALTH=$(curl -sf "${BASE_URL}/healthz" 2>/dev/null || echo '{"status":"down"}')
echo "  /healthz: ${HEALTH}"
if echo "${HEALTH}" | grep -q '"ok"'; then
  echo "  ✓ 服务运行中"
else
  echo "  ✗ 服务未运行，请先执行: docker compose up -d && docker compose exec app alembic upgrade head"
  exit 1
fi

# ── 2. 检查就绪状态 ──
echo ""
echo "[2/7] 检查就绪状态（DB/Redis）..."
READY=$(curl -sf "${BASE_URL}/readyz" 2>/dev/null || echo '{"status":"not_ready"}')
echo "  /readyz: ${READY}"

# ── 3. 创建租户/Key/端点（seed） ──
echo ""
echo "[3/7] 创建演示租户/Key/端点..."
# 使用 Python 一次性脚本创建种子数据
python3 - <<'PYEOF'
import sys, os
sys.path.insert(0, "src")
os.environ.setdefault("LLM_DATABASE_URL", "sqlite:///demo_quickstart.db")

from llm_platform.db.session import init_db, SessionLocal
from llm_platform.auth.api_key import generate_api_key
from llm_platform.db.repositories.tenant_repo import TenantRepo
from llm_platform.db.repositories.key_repo import KeyRepo
from llm_platform.db.repositories.endpoint_repo import EndpointRepo

init_db()
db = SessionLocal()
try:
    tenant = TenantRepo(db).create_tenant("demo-tenant", monthly_budget=100.0)
    project = TenantRepo(db).create_project(tenant.id, "demo-project")
    plaintext, key_hash, key_prefix = generate_api_key()
    KeyRepo(db).create_key(
        tenant_id=tenant.id, project_id=project.id,
        key_hash=key_hash, key_prefix=key_prefix,
        name="demo-key", scopes=["chat", "admin"],
    )
    EndpointRepo(db).create_endpoint(
        name="demo-gpt4o", provider="openai", protocol="openai",
        base_url="https://api.openai.com/v1", model_name="gpt-4o",
        credential_env="LLM_OPENAI_API_KEY", priority=1, weight=1.0,
        cost_per_1k_prompt=0.005, cost_per_1k_completion=0.015,
    )
    print(f"  ✓ 租户 ID={tenant.id}, 项目 ID={project.id}, API Key={plaintext[:12]}...")
    print(f"  ⚠ 完整 Key: {plaintext}")
    with open("demo_api_key.txt", "w") as f:
        f.write(plaintext)
    print("  ✓ Key 已保存到 demo_api_key.txt")
except Exception as e:
    print(f"  ⚠ 数据库初始化: {e}")
    print("  （如使用 docker compose，数据已持久化，可跳过此步）")
finally:
    db.close()
PYEOF

# ── 4. 加载基准集（200 条种子数据） ──
echo ""
echo "[4/7] 加载内置基准集（200 条，5 维度）..."
python3 - <<'PYEOF'
import sys, os
sys.path.insert(0, "src")
os.environ.setdefault("LLM_DATABASE_URL", "sqlite:///demo_quickstart.db")

from llm_platform.db.session import init_db, SessionLocal
from llm_platform.eval.datasets import DatasetManager

init_db()
db = SessionLocal()
try:
    mgr = DatasetManager(db)
    result = mgr.load_seed()
    if result.get("skipped"):
        print(f"  ✓ 基准集已存在（版本 {result['version']}, {result['item_count']} 条）")
    else:
        print(f"  ✓ 基准集已加载: {result['name']} v{result['version']}, {result['item_count']} 条")
    print(f"  checksum: {result['checksum'][:16]}...")
except Exception as e:
    print(f"  ⚠ 基准集加载: {e}")
finally:
    db.close()
PYEOF

# ── 5. 发起 chat 请求 ──
echo ""
echo "[5/7] 发起 chat 请求..."
if [ -f demo_api_key.txt ]; then
  API_KEY=$(cat demo_api_key.txt)
else
  API_KEY="${API_KEY:-sk-demo-key}"
fi

CHAT_RESP=$(curl -sf -X POST "${BASE_URL}/v1/chat/completions" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","messages":[{"role":"user","content":"用一句话介绍大模型统一接入网关的价值。"}],"max_tokens":50}' \
  2>/dev/null || echo '{"error":"请求失败（请检查 API Key 与上游配置）"}')

if echo "${CHAT_RESP}" | grep -q '"choices"'; then
  echo "  ✓ chat 响应成功"
  echo "  $(echo "${CHAT_RESP}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['choices'][0]['message']['content'][:80])" 2>/dev/null || echo '')"
else
  echo "  ⚠ chat 请求未成功（需要配置真实 API Key）"
  echo "  ${CHAT_RESP:0:120}"
fi

# ── 6. 跑一次评测 ──
echo ""
echo "[6/7] 运行评测任务..."
python3 - <<'PYEOF'
import sys, os
sys.path.insert(0, "src")
os.environ.setdefault("LLM_DATABASE_URL", "sqlite:///demo_quickstart.db")

from llm_platform.db.session import init_db, SessionLocal
from llm_platform.eval.datasets import DatasetManager
from llm_platform.eval.metrics import compute_metrics, overall_score

init_db()
db = SessionLocal()
try:
    mgr = DatasetManager(db)
    datasets = mgr.list_datasets()
    if not datasets:
        print("  ⚠ 无基准集，请先执行第 4 步")
    else:
        ds = datasets[0]
        print(f"  基准集: {ds['name']} v{ds['version']} ({ds['item_count']} 条)")
        # 模拟评测（使用固定预测值演示指标计算）
        predictions = ["正确答案", "部分正确", "错误答案", "正确答案", "正确答案"]
        references = ["正确答案", "正确答案", "正确答案", "正确答案", "正确答案"]
        metrics = compute_metrics(predictions, references, latencies_ms=[100,200,150,120,180])
        score = overall_score(metrics)
        print(f"  ✓ 指标: accuracy={metrics.get('accuracy',0):.2f}, f1={metrics.get('f1',0):.2f}, rouge_l={metrics.get('rouge_l',0):.2f}")
        print(f"  ✓ 综合评分: {score:.4f}")
except Exception as e:
    print(f"  ⚠ 评测: {e}")
finally:
    db.close()
PYEOF

# ── 7. 导出账单 ──
echo ""
echo "[7/7] 导出账单..."
python3 - <<'PYEOF'
import sys, os
sys.path.insert(0, "src")
os.environ.setdefault("LLM_DATABASE_URL", "sqlite:///demo_quickstart.db")

from llm_platform.db.session import init_db, SessionLocal
from llm_platform.monitoring.billing import BillingService

init_db()
db = SessionLocal()
try:
    svc = BillingService(db)
    summary = svc.summary()
    print(f"  ✓ 账单汇总: {summary.get('total_requests', 0)} 次调用, 总成本 ${summary.get('total_cost', 0):.6f}")
    csv_data = svc.export_csv()
    lines = csv_data.strip().split("\n")
    print(f"  ✓ CSV 导出: {len(lines) - 1} 行数据")
    print(f"  表头: {lines[0]}")
except Exception as e:
    print(f"  ⚠ 账单: {e}")
finally:
    db.close()
PYEOF

echo ""
echo "═══════════════════════════════════════════"
echo "  演示完成！"
echo ""
echo "  后续操作："
echo "  - Swagger 文档: ${BASE_URL}/docs"
echo "  - Prometheus 指标: ${BASE_URL}/metrics"
echo "  - 运维手册: docs/operations.md"
echo "  - 部署方案: docs/deployment.md"
echo "═══════════════════════════════════════════"
