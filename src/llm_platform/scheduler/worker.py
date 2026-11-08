"""评测 Worker：消费队列、执行评测任务、状态跟踪（P1-06）。

独立进程入口：python -m llm_platform.scheduler.worker
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..db.repositories.eval_repo import EvalRepo
from ..db.session import SessionLocal
from ..eval.runner import EvalRunner
from ..logging import get_logger
from ..models.schemas import (
    EVAL_STATUS_FAILED,
    EVAL_STATUS_RUNNING,
    EVAL_STATUS_SUCCESS,
)
from .queue import EvalQueue
from .state_machine import TaskStateMachine

logger = get_logger("scheduler.worker")


class EvalWorker:
    """评测 Worker：从队列取任务并执行。"""

    def __init__(self, redis_client=None, task_timeout_s: float = 3600.0) -> None:
        self.queue = EvalQueue(redis_client=redis_client)
        self.redis = redis_client
        self.task_timeout_s = task_timeout_s
        self._running = False

    async def process_task(self, task_id: int, payload: dict[str, Any]) -> bool:
        """处理单个评测任务。"""
        db = SessionLocal()
        try:
            repo = EvalRepo(db)
            task = repo.get_task(task_id)
            if not task:
                logger.error("eval_task_not_found", task_id=task_id)
                return False

            # 状态机校验并迁移
            TaskStateMachine.transition(task.status, EVAL_STATUS_RUNNING)
            repo.update_task(task_id, status=EVAL_STATUS_RUNNING)

            try:
                runner = EvalRunner(db, redis_client=self.redis)

                def progress_cb(progress: float) -> None:
                    repo.update_task(task_id, progress=round(progress, 4))

                # 任务级超时（T12）：超过 task_timeout_s 强制标记失败
                reports = await asyncio.wait_for(
                    runner.run_task(
                        task_id=task_id,
                        dataset_id=payload.get("dataset_id", task.dataset_id),
                        models=payload.get("models", task.models),
                        config=payload.get("config", task.config),
                        progress_cb=progress_cb,
                    ),
                    timeout=self.task_timeout_s,
                )

                # 保存报告
                from ..eval.datasets import EVALUATOR_VERSION

                dataset = repo.get_dataset(task.dataset_id)
                dataset_version = dataset.version if dataset else "unknown"

                for report_data in reports:
                    repo.create_report(
                        task_id=task_id,
                        model=report_data["model"],
                        overall_score=report_data["overall_score"],
                        metrics=report_data["metrics"],
                        dataset_version=dataset_version,
                        evaluator_version=EVALUATOR_VERSION,
                    )

                # 状态机：→ success
                TaskStateMachine.transition(EVAL_STATUS_RUNNING, EVAL_STATUS_SUCCESS)
                repo.update_task(task_id, status=EVAL_STATUS_SUCCESS, progress=1.0)
                logger.info("eval_task_completed", task_id=task_id, models=len(reports))
                return True

            except TimeoutError:
                logger.error("eval_task_timeout", task_id=task_id, timeout_s=self.task_timeout_s)
                TaskStateMachine.transition(EVAL_STATUS_RUNNING, EVAL_STATUS_FAILED)
                repo.update_task(
                    task_id, status=EVAL_STATUS_FAILED,
                    error=f"任务超时（{self.task_timeout_s}s）",
                )
                return False
            except Exception as exc:
                logger.error("eval_task_failed", task_id=task_id, error=str(exc))
                TaskStateMachine.transition(EVAL_STATUS_RUNNING, EVAL_STATUS_FAILED)
                repo.update_task(task_id, status=EVAL_STATUS_FAILED, error=str(exc))
                return False

        finally:
            db.close()

    async def run_loop(self) -> None:
        """主循环：从队列消费任务。"""
        self._running = True
        logger.info("eval_worker_started")
        while self._running:
            try:
                item = self.queue.dequeue()
                if item is None:
                    await asyncio.sleep(1)
                    continue

                task_id = item.get("task_id")
                payload = item.get("payload", {})
                success = await self.process_task(task_id, payload)
                if not success:
                    self.queue.retry(task_id, payload, attempt=1)

            except Exception as exc:
                logger.error("eval_worker_error", error=str(exc))
                await asyncio.sleep(5)

    def stop(self) -> None:
        self._running = False


async def main() -> None:
    """Worker 入口。"""
    worker = EvalWorker()
    await worker.run_loop()


if __name__ == "__main__":
    asyncio.run(main())
