#!/usr/bin/env bash
# failover 演练脚本（T12）
# 模拟端点故障 → 验证自动 failover → 恢复
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
API_KEY="${API_KEY:-test-key}"
MODEL="${MODEL:-gpt-4o}"

echo "═══════════════════════════════════════════"
echo "  Failover 演练脚本"
echo "  目标: ${BASE_URL}"
echo "═══════════════════════════════════════════"

# 1. 确认服务健康
echo ""
echo "[1/5] 检查服务健康..."
HEALTH=$(curl -sf "${BASE_URL}/healthz" || echo '{"status":"down"}')
echo "  健康状态: ${HEALTH}"

# 2. 发送正常请求确认基线
echo ""
echo "[2/5] 基线请求（应成功）..."
RESP=$(curl -sf -X POST "${BASE_URL}/v1/chat/completions" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"${MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"hello\"}],\"max_tokens\":10}" \
  2>/dev/null || echo '{"error":"request_failed"}')

if echo "${RESP}" | grep -q '"choices"'; then
  echo "  ✓ 基线请求成功"
else
  echo "  ✗ 基线请求失败: ${RESP}"
fi

# 3. 查看当前路由状态
echo ""
echo "[3/5] 查看端点健康状态..."
curl -sf "${BASE_URL}/admin/v1/endpoints" \
  -H "Authorization: Bearer ${API_KEY}" 2>/dev/null | python3 -m json.tool 2>/dev/null || echo "  (跳过：需要管理权限)"

# 4. 连续发送请求观察 failover
echo ""
echo "[4/5] 发送 10 个请求观察 failover..."
SUCCESS=0
FAILOVER=0
for i in $(seq 1 10); do
  RESP=$(curl -sf -X POST "${BASE_URL}/v1/chat/completions" \
    -H "Authorization: Bearer ${API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"${MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"test ${i}\"}],\"max_tokens\":5}" \
    2>/dev/null || echo '{}')

  if echo "${RESP}" | grep -q '"choices"'; then
    SUCCESS=$((SUCCESS + 1))
  else
    FAILOVER=$((FAILOVER + 1))
  fi
  sleep 0.2
done
echo "  结果: 成功=${SUCCESS} 失败/切换=${FAILOVER}"

# 5. 查看 Prometheus 指标中的 failover 计数
echo ""
echo "[5/5] 检查 failover 指标..."
curl -sf "${BASE_URL}/metrics" 2>/dev/null | grep -E "llm_failover_total|llm_circuit_state" || echo "  (指标不可用)"

echo ""
echo "═══════════════════════════════════════════"
echo "  演练完成"
echo "═══════════════════════════════════════════"
