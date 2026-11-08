"""评测管理 API（P1-01~P1-04）：数据集/任务/报告/对比/导出。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ...auth.audit import audit_log
from ...auth.service import AuthContext
from ...db.repositories.eval_repo import EvalRepo
from ...deps import DbSession, RedisClient, require_evaluator
from ...errors import NotFoundError, ValidationError
from ...eval.datasets import DatasetManager
from ...eval.report import ReportGenerator
from ...logging import get_logger

logger = get_logger("api.admin.evals")
router = APIRouter(prefix="/admin/v1", tags=["admin-evals"])


# ── 请求/响应模型 ──

class DatasetItemIn(BaseModel):
    input_prompt: str
    expected_output: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class DatasetCreate(BaseModel):
    name: str
    version: str
    items: list[DatasetItemIn]
    source: str = ""


class EvalTaskCreate(BaseModel):
    name: str
    dataset_id: int
    models: list[str]
    config: dict[str, Any] = Field(default_factory=dict)


class EvalTaskOut(BaseModel):
    id: int
    name: str
    status: str
    progress: float
    models: list
    dataset_id: int
    created_at: Any = None

    model_config = {"from_attributes": True}


# ── 数据集 API ──

@router.post("/eval-datasets", status_code=201)
async def create_dataset(
    body: DatasetCreate,
    request: Request,
    db: DbSession,
    auth: AuthContext = Depends(require_evaluator),
):
    """注册基准集（版本+checksum）。"""
    mgr = DatasetManager(db)
    items = [it.model_dump() for it in body.items]
    result = mgr.register(body.name, body.version, items, source=body.source)

    audit_log(
        db,
        actor_type="jwt_user",
        actor_id=str(auth.api_key_id),
        action="eval_dataset.create",
        resource_type="eval_dataset",
        resource_id=str(result["id"]),
        ip=request.client.host if request.client else None,
        detail={"name": body.name, "version": body.version, "item_count": result["item_count"]},
    )
    return result


@router.get("/eval-datasets")
async def list_datasets(
    db: DbSession,
    auth: AuthContext = Depends(require_evaluator),
):
    """基准集列表。"""
    mgr = DatasetManager(db)
    return {"items": mgr.list_datasets()}


# ── 任务 API ──

@router.post("/eval-tasks", status_code=202)
async def create_eval_task(
    body: EvalTaskCreate,
    request: Request,
    db: DbSession,
    redis: RedisClient,
    auth: AuthContext = Depends(require_evaluator),
):
    """启动评测（异步执行）。"""
    from ...models.schemas import EVAL_STATUS_PENDING

    # 检查是否有同名运行中任务
    repo = EvalRepo(db)
    # 简单检查 dataset 存在
    dataset = repo.get_dataset(body.dataset_id)
    if not dataset:
        raise NotFoundError(message=f"基准集不存在: {body.dataset_id}")

    task = repo.create_task(
        name=body.name,
        dataset_id=body.dataset_id,
        models=body.models,
        config=body.config,
        status=EVAL_STATUS_PENDING,
        progress=0.0,
        created_by=str(auth.api_key_id),
    )

    # 入队（异步执行）
    from ...scheduler.queue import EvalQueue

    queue = EvalQueue(redis_client=redis)
    queue.enqueue(task.id, {
        "dataset_id": body.dataset_id,
        "models": body.models,
        "config": body.config,
    })

    audit_log(
        db,
        actor_type="jwt_user",
        actor_id=str(auth.api_key_id),
        action="eval_task.create",
        resource_type="eval_task",
        resource_id=str(task.id),
        ip=request.client.host if request.client else None,
        detail={"name": body.name, "models": body.models},
    )

    return {
        "id": task.id,
        "status": task.status,
        "progress": task.progress,
        "models": body.models,
    }


@router.get("/eval-tasks/{task_id}")
async def get_eval_task(
    task_id: int,
    db: DbSession,
    auth: AuthContext = Depends(require_evaluator),
):
    """任务状态/进度。"""
    repo = EvalRepo(db)
    task = repo.get_task(task_id)
    if not task:
        raise NotFoundError(message=f"任务不存在: {task_id}")
    return EvalTaskOut.model_validate(task)


@router.post("/eval-tasks/{task_id}/cancel")
async def cancel_eval_task(
    task_id: int,
    request: Request,
    db: DbSession,
    auth: AuthContext = Depends(require_evaluator),
):
    """取消任务。"""
    from ...models.schemas import EVAL_STATUS_CANCELLED
    from ...scheduler.state_machine import TaskStateMachine

    repo = EvalRepo(db)
    task = repo.get_task(task_id)
    if not task:
        raise NotFoundError(message=f"任务不存在: {task_id}")

    TaskStateMachine.transition(task.status, EVAL_STATUS_CANCELLED)
    repo.update_task(task_id, status=EVAL_STATUS_CANCELLED)

    audit_log(
        db,
        actor_type="jwt_user",
        actor_id=str(auth.api_key_id),
        action="eval_task.cancel",
        resource_type="eval_task",
        resource_id=str(task_id),
        ip=request.client.host if request.client else None,
    )
    return {"id": task_id, "status": EVAL_STATUS_CANCELLED}


@router.get("/eval-tasks/{task_id}/reports")
async def get_eval_reports(
    task_id: int,
    db: DbSession,
    auth: AuthContext = Depends(require_evaluator),
):
    """报告列表。"""
    repo = EvalRepo(db)
    task = repo.get_task(task_id)
    if not task:
        raise NotFoundError(message=f"任务不存在: {task_id}")

    reports = repo.list_reports(task_id)
    dataset = repo.get_dataset(task.dataset_id)

    return {
        "task_id": task_id,
        "dataset_version": dataset.version if dataset else "unknown",
        "evaluator_version": "v1",
        "items": [
            {
                "model": r.model,
                "overall_score": r.overall_score,
                "metrics": r.metrics,
            }
            for r in reports
        ],
    }


# ── 对比分析 API ──

@router.get("/eval-reports/compare")
async def compare_eval_reports(
    db: DbSession,
    models: str = "",
    task_id: int | None = None,
    auth: AuthContext = Depends(require_evaluator),
):
    """多模型对比（P1-04）。"""
    repo = EvalRepo(db)

    if task_id:
        reports = repo.list_reports(task_id)
    else:
        # 查询最近的报告（简化：取最新任务的报告）
        reports = []
        # TODO M3: 支持跨任务对比

    model_list = [m.strip() for m in models.split(",") if m.strip()] if models else []
    if model_list:
        reports = [r for r in reports if r.model in model_list]

    report_dicts = [
        {
            "model": r.model,
            "overall_score": r.overall_score,
            "metrics": r.metrics,
        }
        for r in reports
    ]

    generator = ReportGenerator()
    result = generator.compare(report_dicts)
    return result


@router.get("/eval-reports/{report_id}/export")
async def export_eval_report(
    report_id: int,
    db: DbSession,
    format: str = "json",  # noqa: A002
    auth: AuthContext = Depends(require_evaluator),
):
    """导出 JSON/PNG（P1-03）。"""
    from ...models.entities import EvalReport

    EvalRepo(db)
    report = db.get(EvalReport, report_id)
    if not report:
        raise NotFoundError(message=f"报告不存在: {report_id}")

    report_dict = {
        "model": report.model,
        "overall_score": report.overall_score,
        "metrics": report.metrics,
    }

    generator = ReportGenerator()
    if format == "json":
        return {"format": "json", "content": generator.export_json(report_dict)}
    elif format == "png":
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            path = f.name
        ok = generator.export_png(report_dict, path)
        if ok:
            from fastapi.responses import FileResponse

            return FileResponse(path, media_type="image/png", filename=f"eval-report-{report_id}.png")
        return {"format": "png", "error": "matplotlib 不可用"}
    else:
        raise ValidationError(message=f"不支持的格式: {format}")
