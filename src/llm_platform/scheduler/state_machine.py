"""任务状态机：pending→running→success/failed/cancelled 迁移校验（P1-06）。"""

from __future__ import annotations

from ..errors import ConflictError
from ..logging import get_logger
from ..models.schemas import (
    EVAL_STATUS_CANCELLED,
    EVAL_STATUS_FAILED,
    EVAL_STATUS_PENDING,
    EVAL_STATUS_RUNNING,
    EVAL_STATUS_SUCCESS,
)

logger = get_logger("scheduler.state_machine")

# 允许的状态迁移
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    EVAL_STATUS_PENDING: {EVAL_STATUS_RUNNING, EVAL_STATUS_CANCELLED},
    EVAL_STATUS_RUNNING: {EVAL_STATUS_SUCCESS, EVAL_STATUS_FAILED, EVAL_STATUS_CANCELLED},
    EVAL_STATUS_SUCCESS: set(),
    EVAL_STATUS_FAILED: {EVAL_STATUS_PENDING},  # 允许重试
    EVAL_STATUS_CANCELLED: set(),
}


class TaskStateMachine:
    """评测任务状态机。"""

    @staticmethod
    def validate_transition(current: str, target: str) -> bool:
        """校验状态迁移是否合法。"""
        allowed = ALLOWED_TRANSITIONS.get(current, set())
        return target in allowed

    @staticmethod
    def transition(current: str, target: str) -> str:
        """执行状态迁移，非法迁移抛 ConflictError。"""
        if not TaskStateMachine.validate_transition(current, target):
            raise ConflictError(
                message=f"非法状态迁移: {current} → {target}",
                current=current,
                target=target,
            )
        logger.info("task_state_transition", from_state=current, to_state=target)
        return target

    @staticmethod
    def is_terminal(state: str) -> bool:
        """是否为终态。"""
        return not ALLOWED_TRANSITIONS.get(state, set())
