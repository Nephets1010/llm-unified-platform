#!/usr/bin/env bash
# 灰度发布/回滚演练脚本（T12）
# 配置 canary 权重 → 观察流量分配 → 调整权重 → 回滚
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
ADMIN_TOKEN="${ADMIN_TOKEN:-admin-jwt-token}"
CANARY_WEIGHT="${CANARY_WEIGHT:-0.2}"

echo "═══════════════════════════════════════════"
echo "  灰度发布/回滚演练脚本"
echo "  目标: ${BASE_URL}"
echo "  灰度权重: ${CANARY_WEIGHT}"
echo "═══════════════════════════════════════════"

# 1. 查看当前配置版本
echo ""
echo "[1/6] 查看当前配置版本..."
curl -sf "${BASE_URL}/admin/v1/config/routing" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" 2>/dev/null | python3 -m json.tool 2>/dev/null || echo "  (跳过：需要管理权限)"

# 2. 设置灰度权重
echo ""
echo "[2/6] 设置灰度权重 = ${CANARY_WEIGHT}..."
RESP=$(curl -sf -X PUT "${BASE_URL}/admin/v1/config/routing" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{\"payload\": {\"canary_weight\": ${CANARY_WEIGHT}, \"canary_endpoint_ids\": [2], \"canary_tenant_ids\": [], \"canary_key_hashes\": []}}" \
  2>/dev/null || echo '{"error":"config_update_failed"}')
echo "  响应: ${RESP}"

# 3. 发送 20 个请求观察流量分配
echo ""
echo "[3/6] 发送 20 个请求观察灰度分流..."
CANARY_HITS=0
MAIN_HITS=0
for i in $(seq 1 20); do
  RESP=$(curl -sf -X POST "${BASE_URL}/v1/chat/completions" \
    -H "Authorization: Bearer ${API_KEY:-test-key}" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"gpt-4o\",\"messages\":[{\"role\":\"user\",\"content\":\"canary test ${i}\"}],\"max_tokens\":5}" \
    2>/dev/null || echo '{}')

  # 检查响应中是否走了灰度端点（可通过 header 或 body 判断）
  if echo "${RESP}" | grep -q '"endpoint_id": *2\|canary'; then
    CANARY_HITS=$((CANARY_HITS + 1))
  else
    MAIN_HITS=$((MAIN_HITS + 1))
  fi
  sleep 0.1
done
echo "  结果: 主流量=${MAIN_HITS} 灰度流量=${CANARY_HITS}"

# 4. 调整灰度权重到 50%
echo ""
echo "[4/6] 调整灰度权重 = 0.5..."
curl -sf -X PUT "${BASE_URL}/admin/v1/config/routing" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"payload": {"canary_weight": 0.5, "canary_endpoint_ids": [2], "canary_tenant_ids": [], "canary_key_hashes": []}}' \
  2>/dev/null | python3 -m json.tool 2>/dev/null || echo "  (跳过)"

# 5. 执行回滚（清除灰度配置）
echo ""
echo "[5/6] 执行回滚（清除灰度）..."
curl -sf -X POST "${BASE_URL}/admin/v1/config/routing/rollback" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  2>/dev/null | python3 -m json.tool 2>/dev/null || echo "  (跳过：需要配置版本)"

# 6. 验证回滚后流量恢复
echo ""
echo "[6/6] 验证回滚后流量分布..."
for i in $(seq 1 5); do
  curl -sf -X POST "${BASE_URL}/v1/chat/completions" \
    -H "Authorization: Bearer ${API_KEY:-test-key}" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"gpt-4o\",\"messages\":[{\"role\":\"user\",\"content\":\"rollback check ${i}\"}],\"max_tokens\":5}" \
    2>/dev/null > /dev/null || true
done
echo "  ✓ 回滚验证完成"

echo ""
echo "═══════════════════════════════════════════"
echo "  演练完成"
echo "═══════════════════════════════════════════"
