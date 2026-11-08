"""基准集管理：注册、版本化、checksum 防篡改、内置种子（P1-02）。

内置种子：≥200 条评测样本，覆盖 5 维度（text_gen/reasoning/code/summarization/safety），
见 seed_data.py。加载方式：python -m llm_platform.eval.seed_data --load

基准集版本化：eval_dataset.version + checksum，保证可复现。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy.orm import Session

from ..db.repositories.eval_repo import EvalRepo
from ..errors import ConflictError, NotFoundError, ValidationError
from ..logging import get_logger

logger = get_logger("eval.datasets")

EVALUATOR_VERSION = "v1"


def normalize_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """规范化样本形状：统一补齐 input_prompt / expected_output / metadata 键。

    保证 register 与 get_items 对同一逻辑数据计算出相同 checksum，
    消除"无 metadata 键注册 → get_items 重算必误报"的规范化不一致。
    """
    return [
        {
            "input_prompt": it.get("input_prompt", ""),
            "expected_output": it.get("expected_output", ""),
            "metadata": it.get("metadata", {}),
        }
        for it in items
    ]


def compute_checksum(items: list[dict[str, Any]]) -> str:
    """计算基准集内容指纹（SHA-256），基于规范化形状。"""
    normalized = normalize_items(items)
    canonical = json.dumps(normalized, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class DatasetManager:
    """基准集管理器：注册/版本/checksum 校验。"""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = EvalRepo(db)

    def register(
        self,
        name: str,
        version: str,
        items: list[dict[str, Any]],
        source: str = "",
    ) -> dict[str, Any]:
        """注册基准集。

        Args:
            name: 基准集名称。
            version: 语义化版本（如 "1.0.0"）。
            items: 样本列表，每项含 input_prompt / expected_output / metadata。
            source: 来源标识（如 "builtin-seed"、"user-upload"、"benchmark-suite"）。

        Returns:
            基准集信息。

        Raises:
            ValidationError: items 为空或格式非法。
            ConflictError: version 已存在。
        """
        if not items:
            raise ValidationError(message="基准集样本不能为空")
        for i, item in enumerate(items):
            if "input_prompt" not in item:
                raise ValidationError(message=f"第 {i} 条样本缺少 input_prompt", index=i)

        # 检查版本唯一性
        existing = self.repo.list_datasets()
        for ds in existing:
            if ds.version == version:
                raise ConflictError(message=f"版本已存在: {version}", version=version)

        checksum = compute_checksum(items)
        dataset = self.repo.create_dataset(
            name=name,
            version=version,
            item_count=len(items),
            checksum=checksum,
            source=source,
        )

        # 存储样本
        item_dicts = [
            {
                "input_prompt": it["input_prompt"],
                "expected_output": it.get("expected_output", ""),
                "metadata_": it.get("metadata", {}),
            }
            for it in items
        ]
        self.repo.add_dataset_items(dataset.id, item_dicts)

        logger.info(
            "dataset_registered",
            dataset_id=dataset.id,
            name=name,
            version=version,
            item_count=len(items),
        )
        return {
            "id": dataset.id,
            "name": name,
            "version": version,
            "item_count": len(items),
            "checksum": checksum,
        }

    def verify_checksum(self, dataset_id: int, items: list[dict[str, Any]]) -> bool:
        """验证基准集 checksum 一致性。"""
        dataset = self.repo.get_dataset(dataset_id)
        if not dataset:
            raise NotFoundError(message=f"基准集不存在: {dataset_id}")
        return compute_checksum(items) == dataset.checksum

    def get_items(self, dataset_id: int) -> list[dict[str, Any]]:
        """获取基准集全部样本。"""
        from sqlalchemy import select

        from ..models.entities import EvalDatasetItem

        stmt = select(EvalDatasetItem).where(EvalDatasetItem.dataset_id == dataset_id)
        rows = self.db.execute(stmt).scalars().all()
        return [
            {
                "input_prompt": r.input_prompt,
                "expected_output": r.expected_output,
                "metadata": r.metadata_ or {},
            }
            for r in rows
        ]

    def list_datasets(self) -> list[dict[str, Any]]:
        """列出全部基准集。"""
        datasets = self.repo.list_datasets()
        return [
            {
                "id": d.id,
                "name": d.name,
                "version": d.version,
                "item_count": d.item_count,
                "checksum": d.checksum,
                "source": d.source,
            }
            for d in datasets
        ]

    def load_seed(self, name: str = "builtin-benchmark", version: str = "1.0.0") -> dict[str, Any]:
        """加载内置基准集种子（≥200 条，5 维度）。

        如果同版本已存在则跳过，保证幂等。

        Returns:
            基准集信息。
        """
        from .seed_data import get_seed_items

        items = get_seed_items()
        # 幂等检查
        for ds in self.repo.list_datasets():
            if ds.version == version:
                return {
                    "id": ds.id,
                    "name": ds.name,
                    "version": ds.version,
                    "item_count": ds.item_count,
                    "checksum": ds.checksum,
                    "skipped": True,
                }
        return self.register(name, version, items, source="builtin-seed")
