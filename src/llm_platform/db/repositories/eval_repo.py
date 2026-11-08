"""评测仓储（M2 预留，M1 建表）。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models.entities import EvalDataset, EvalDatasetItem, EvalReport, EvalTask


class EvalRepo:
    """评测相关表 CRUD。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    def create_dataset(self, **kwargs) -> EvalDataset:
        ds = EvalDataset(**kwargs)
        self.db.add(ds)
        self.db.commit()
        self.db.refresh(ds)
        return ds

    def get_dataset(self, dataset_id: int) -> EvalDataset | None:
        return self.db.get(EvalDataset, dataset_id)

    def list_datasets(self) -> list[EvalDataset]:
        return list(self.db.execute(select(EvalDataset)).scalars().all())

    def add_dataset_items(self, dataset_id: int, items: list[dict]) -> int:
        objs = [EvalDatasetItem(dataset_id=dataset_id, **item) for item in items]
        self.db.add_all(objs)
        self.db.commit()
        return len(objs)

    def create_task(self, **kwargs) -> EvalTask:
        task = EvalTask(**kwargs)
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        return task

    def get_task(self, task_id: int) -> EvalTask | None:
        return self.db.get(EvalTask, task_id)

    def update_task(self, task_id: int, **kwargs) -> EvalTask | None:
        task = self.get_task(task_id)
        if not task:
            return None
        for k, v in kwargs.items():
            if v is not None and hasattr(task, k):
                setattr(task, k, v)
        self.db.commit()
        self.db.refresh(task)
        return task

    def create_report(self, **kwargs) -> EvalReport:
        report = EvalReport(**kwargs)
        self.db.add(report)
        self.db.commit()
        self.db.refresh(report)
        return report

    def list_reports(self, task_id: int) -> list[EvalReport]:
        stmt = select(EvalReport).where(EvalReport.task_id == task_id)
        return list(self.db.execute(stmt).scalars().all())
