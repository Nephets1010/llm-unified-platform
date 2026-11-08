"""评估引擎模块：基准集管理、指标计算、批量推理、报告生成（ADR-005）。"""

from .datasets import DatasetManager
from .metrics import METRIC_NAMES, compute_metrics
from .report import ReportGenerator
from .runner import EvalRunner

__all__ = ["DatasetManager", "compute_metrics", "METRIC_NAMES", "EvalRunner", "ReportGenerator"]
