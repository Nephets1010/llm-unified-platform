"""报告生成：聚合评分、多模型对比、JSON/PNG 导出（P1-03/P1-04）。"""

from __future__ import annotations

import json
from typing import Any

from ..logging import get_logger
from .datasets import EVALUATOR_VERSION

logger = get_logger("eval.report")


class ReportGenerator:
    """评测报告生成器。"""

    def generate(
        self,
        task_id: int,
        model: str,
        metrics: dict[str, Any],
        overall: float,
        dataset_version: str,
    ) -> dict[str, Any]:
        """生成单模型报告。"""
        return {
            "task_id": task_id,
            "model": model,
            "overall_score": overall,
            "metrics": metrics,
            "dataset_version": dataset_version,
            "evaluator_version": EVALUATOR_VERSION,
        }

    def compare(
        self,
        reports: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """多模型/多版本对比分析（P1-04）。

        Returns:
            {"dimensions": [...], "series": [...], "conclusion": {...}}
        """
        if not reports:
            return {"dimensions": [], "series": [], "conclusion": {}}

        # 收集所有指标维度
        all_dims: list[str] = []
        for r in reports:
            for k in r.get("metrics", {}):
                if k not in all_dims:
                    all_dims.append(k)

        # 构建 series
        series = []
        for r in reports:
            values = [r.get("metrics", {}).get(d) for d in all_dims]
            series.append({
                "model": r["model"],
                "values": values,
                "overall_score": r.get("overall_score", 0),
            })

        # 结论：按 overall_score 选出 winner
        winner_entry = max(series, key=lambda s: s.get("overall_score", 0))
        winner = winner_entry["model"]

        # diff：winner vs 每个其他模型
        diff: dict[str, float] = {}
        if len(series) >= 2:
            others = [s for s in series if s["model"] != winner]
            if others:
                runner_up = others[0]
                for i, dim in enumerate(all_dims):
                    w_val = winner_entry["values"][i]
                    o_val = runner_up["values"][i]
                    if isinstance(w_val, (int, float)) and isinstance(o_val, (int, float)):
                        diff[dim] = round(w_val - o_val, 6)

        return {
            "dimensions": all_dims,
            "series": series,
            "conclusion": {
                "winner": winner,
                "diff": diff,
            },
        }

    def compare_cross_task(
        self,
        task_reports: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """④ 跨任务对比：比较不同任务/数据集版本下的模型表现。

        Args:
            task_reports: 多个任务的报告列表，每项含 task_id/model/metrics/overall_score。

        Returns:
            {"tasks": [...], "model_trends": {...}, "best_task": {...}}
        """
        if not task_reports:
            return {"tasks": [], "model_trends": {}, "best_task": {}}

        # 按模型聚合趋势
        model_trends: dict[str, list[dict]] = {}
        for r in task_reports:
            model = r.get("model", "unknown")
            if model not in model_trends:
                model_trends[model] = []
            model_trends[model].append({
                "task_id": r.get("task_id"),
                "overall_score": r.get("overall_score", 0),
                "metrics": r.get("metrics", {}),
            })

        # 找最优任务（overall_score 最高）
        best = max(task_reports, key=lambda r: r.get("overall_score", 0))

        return {
            "tasks": [
                {
                    "task_id": r.get("task_id"),
                    "model": r.get("model"),
                    "overall_score": r.get("overall_score", 0),
                }
                for r in task_reports
            ],
            "model_trends": model_trends,
            "best_task": {
                "task_id": best.get("task_id"),
                "model": best.get("model"),
                "overall_score": best.get("overall_score", 0),
            },
        }

    def export_json(self, report: dict[str, Any]) -> str:
        """导出 JSON 格式报告。"""
        return json.dumps(report, ensure_ascii=False, indent=2)

    def export_png(self, report: dict[str, Any], output_path: str) -> bool:
        """导出 PNG 雷达图（matplotlib，可选依赖降级）。"""
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            import numpy as np

            dims = list(report.get("metrics", {}).keys())
            values = [report["metrics"][d] for d in dims]

            if not dims:
                return False

            # 雷达图
            angles = np.linspace(0, 2 * np.pi, len(dims), endpoint=False).tolist()
            values_plot = values + [values[0]]
            angles += angles[:1]

            fig, ax = plt.subplots(figsize=(6, 6), subplot_kw=dict(polar=True))
            ax.fill(angles, values_plot, alpha=0.25)
            ax.plot(angles, values_plot)
            ax.set_xticks(angles[:-1])
            ax.set_xticklabels(dims)
            ax.set_title(f"Eval Report: {report.get('model', 'unknown')}")
            plt.tight_layout()
            plt.savefig(output_path, dpi=150)
            plt.close()
            return True
        except ImportError:
            logger.warning("matplotlib_not_available", output_path=output_path)
            return False
