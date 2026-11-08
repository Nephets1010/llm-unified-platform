"""内置基准集种子测试（P1-02）：≥200 条、5 维度覆盖、checksum 确定性。"""

from __future__ import annotations

import uuid

import pytest

from llm_platform.db.session import SessionLocal, init_db
from llm_platform.eval.datasets import DatasetManager
from llm_platform.eval.seed_data import (
    get_dimension_counts,
    get_seed_checksum,
    get_seed_items,
)


@pytest.fixture(autouse=True)
def setup_db():
    init_db()
    yield


class TestSeedData:
    def test_item_count_at_least_200(self):
        """P1-02：基准集 ≥200 条。"""
        items = get_seed_items()
        assert len(items) >= 200, f"基准集仅 {len(items)} 条，要求 ≥200"

    def test_five_dimensions_covered(self):
        """P1-02：覆盖 5 个维度。"""
        counts = get_dimension_counts()
        assert len(counts) == 5, f"维度数 {len(counts)}，要求 5 个"
        for dim in ("text_gen", "reasoning", "code", "summarization", "safety"):
            assert dim in counts, f"缺少维度: {dim}"

    def test_each_dimension_at_least_30(self):
        """P1-02：每维度 ≥30 条。"""
        counts = get_dimension_counts()
        for dim, cnt in counts.items():
            assert cnt >= 30, f"维度 {dim} 仅 {cnt} 条，要求 ≥30"

    def test_checksum_deterministic(self):
        """checksum 确定性：多次计算结果一致。"""
        cs1 = get_seed_checksum()
        cs2 = get_seed_checksum()
        assert cs1 == cs2
        assert len(cs1) == 64

    def test_item_format(self):
        """每条样本含 input_prompt 和 expected_output。"""
        items = get_seed_items()
        for i, item in enumerate(items[:10]):
            assert "input_prompt" in item, f"第 {i} 条缺少 input_prompt"
            assert "expected_output" in item, f"第 {i} 条缺少 expected_output"
            assert item["input_prompt"], f"第 {i} 条 input_prompt 为空"

    def test_register_seed_dataset(self):
        """种子数据可成功注册到 DB 并校验 checksum。"""
        db = SessionLocal()
        try:
            mgr = DatasetManager(db)
            items = get_seed_items()
            uid = uuid.uuid4().hex[:6]
            result = mgr.register(f"test-seed-{uid}", f"1.0.{uid}", items, source="test-seed")
            assert result["item_count"] == len(items)
            assert mgr.verify_checksum(result["id"], items) is True
        finally:
            db.close()
