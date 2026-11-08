"""评测任务队列：Redis 队列 + 失败重试（指数退避）（P1-06）。

q:eval 队列：LPUSH 入队 / BRPOP 出队。
"""

from __future__ import annotations

import json
from typing import Any

from ..logging import get_logger

logger = get_logger("scheduler.queue")

QUEUE_KEY = "q:eval"
MAX_RETRIES = 3
BASE_BACKOFF_S = 5  # 秒


class EvalQueue:
    """评测任务队列（Redis 或本地降级）。"""

    def __init__(self, redis_client=None) -> None:
        self.redis = redis_client
        self._local_queue: list[dict[str, Any]] = []
        self._failed: list[dict[str, Any]] = []

    def enqueue(self, task_id: int, payload: dict[str, Any]) -> bool:
        """入队。"""
        message = json.dumps({"task_id": task_id, "payload": payload}, ensure_ascii=False)
        if self.redis:
            try:
                self.redis.lpush(QUEUE_KEY, message)  # type: ignore[union-attr]
                return True
            except Exception as exc:
                logger.warning("redis_enqueue_failed", error=str(exc))
        self._local_queue.append({"task_id": task_id, "payload": payload})
        return True

    def dequeue(self) -> dict[str, Any] | None:
        """出队（阻塞式语义，本地实现为非阻塞）。"""
        if self.redis:
            try:
                result = self.redis.brpop(QUEUE_KEY, timeout=1)  # type: ignore[union-attr]
                if result:
                    data = json.loads(result[1])
                    return data
            except Exception as exc:
                logger.warning("redis_dequeue_failed", error=str(exc))

        if self._local_queue:
            return self._local_queue.pop(0)
        return None

    def retry(self, task_id: int, payload: dict[str, Any], attempt: int) -> bool:
        """失败重试（指数退避）。"""
        if attempt >= MAX_RETRIES:
            logger.error("eval_task_max_retries", task_id=task_id, attempt=attempt)
            self._failed.append({"task_id": task_id, "payload": payload, "attempt": attempt})
            return False

        # 指数退避：5s, 10s, 20s...
        backoff = BASE_BACKOFF_S * (2 ** (attempt - 1))
        logger.info("eval_task_retry", task_id=task_id, attempt=attempt, backoff_s=backoff)

        # M1 简化：直接重新入队（实际生产可用 sorted set 延迟队列）
        return self.enqueue(task_id, payload)

    def size(self) -> int:
        if self.redis:
            try:
                return int(self.redis.llen(QUEUE_KEY))  # type: ignore[union-attr]
            except Exception:
                pass
        return len(self._local_queue)
