"""种子 call_log 数据生成脚本（T11）。

生成模拟调用日志用于账单/告警/仪表盘冒烟测试。

用法：
    python scripts/seed_call_log.py --count 1000
    python scripts/seed_call_log.py --count 500 --days 7
"""

from __future__ import annotations

import argparse
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from llm_platform.db.session import SessionLocal, engine, Base  # noqa: E402
from llm_platform.models.entities import CallLog  # noqa: E402
from llm_platform.models.schemas import CALL_STATUS_ERROR, CALL_STATUS_OK, CALL_STATUS_RATE_LIMITED, SOURCE_API  # noqa: E402


MODELS = ["gpt-4o", "gpt-4o-mini", "deepseek-chat", "qwen-turbo", "claude-3-5-sonnet"]
PROVIDERS = ["openai", "openai", "deepseek", "qwen", "anthropic"]
ERROR_CODES = ["UPSTREAM_001", "UPSTREAM_004", "RATELIMIT_001", "SYS_000"]


def generate_call_logs(count: int, days: int) -> list[dict]:
    """生成模拟 call_log 记录。"""
    now = datetime.now(timezone.utc)
    records = []
    for _ in range(count):
        model_idx = random.randint(0, len(MODELS) - 1)
        model = MODELS[model_idx]
        provider = PROVIDERS[model_idx]

        # 随机时间（最近 N 天内）
        offset_s = random.uniform(0, days * 86400)
        ts = now - timedelta(seconds=offset_s)

        # 90% 成功，7% 限流，3% 错误
        r = random.random()
        if r < 0.90:
            status = CALL_STATUS_OK
            error_code = None
        elif r < 0.97:
            status = CALL_STATUS_RATE_LIMITED
            error_code = "RATELIMIT_001"
        else:
            status = CALL_STATUS_ERROR
            error_code = random.choice(ERROR_CODES)

        prompt_tokens = random.randint(50, 2000)
        completion_tokens = random.randint(20, 1500)
        latency_ms = int(random.gauss(800, 300))
        if latency_ms < 50:
            latency_ms = 50

        cost_per_1k = {"gpt-4o": 0.005, "gpt-4o-mini": 0.00015, "deepseek-chat": 0.0002, "qwen-turbo": 0.0001, "claude-3-5-sonnet": 0.003}
        cost = (prompt_tokens + completion_tokens) / 1000.0 * cost_per_1k.get(model, 0.001)

        records.append({
            "tenant_id": random.randint(1, 3),
            "project_id": random.randint(1, 5),
            "endpoint_id": random.randint(1, 8),
            "trace_id": str(uuid.uuid4()),
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "tokens_estimated": random.random() < 0.3,
            "cost": round(cost, 6),
            "latency_ms": latency_ms,
            "first_token_latency_ms": int(latency_ms * 0.3) if random.random() < 0.5 else None,
            "status": status,
            "error_code": error_code,
            "source": SOURCE_API,
            "attempt_count": random.choice([1, 1, 1, 2]),
            "route_strategy": random.choice(["cost_first", "latency_first", "weighted_round_robin", "health_aware"]),
            "prompt_hash": uuid.uuid4().hex[:64],
            "created_at": ts,
        })
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="生成种子 call_log 数据")
    parser.add_argument("--count", type=int, default=1000, help="生成记录数")
    parser.add_argument("--days", type=int, default=7, help="时间跨度（天）")
    args = parser.parse_args()

    # 确保表存在
    Base.metadata.create_all(engine)

    records = generate_call_logs(args.count, args.days)
    db = SessionLocal()
    try:
        db.bulk_insert_mappings(CallLog, records)
        db.commit()
        print(f"✓ 已插入 {len(records)} 条 call_log 记录（{args.days} 天跨度）")
    except Exception as exc:
        db.rollback()
        print(f"✗ 插入失败: {exc}")
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
