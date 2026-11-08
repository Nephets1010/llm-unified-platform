"""评测指标：纯函数实现，可离线重算（ADR-005 / P1-01）。

支持指标：
- accuracy: 精确匹配准确率
- f1: token 级 F1
- rouge_l: ROUGE-L（最长公共子序列）
- latency_p95_ms: P95 延迟
- tokens: 总 Token 数
- cost_usd: 总成本
- tool_success_rate: 工具调用成功率（P1-05）
- bertscore: BERTScore（可选，缺 torch 自动降级为 None）
"""

from __future__ import annotations

import math
from typing import Any

METRIC_NAMES = [
    "accuracy",
    "f1",
    "rouge_l",
    "latency_p95_ms",
    "tokens",
    "cost_usd",
    "tool_success_rate",
]

# 可选指标（依赖重，缺失时降级）
OPTIONAL_METRICS = ["bertscore"]


def _tokenize(text: str) -> list[str]:
    """简单分词：按空白 + 标点切分（中英文兼容）。"""
    import re

    tokens = re.findall(r"[\w\u4e00-\u9fff]+", text.lower())
    return tokens


def accuracy(predictions: list[str], references: list[str]) -> float:
    """精确匹配准确率。"""
    if not predictions or len(predictions) != len(references):
        return 0.0
    correct = sum(1 for p, r in zip(predictions, references, strict=False) if p.strip() == r.strip())
    return correct / len(predictions)


def f1_score(predictions: list[str], references: list[str]) -> float:
    """Token 级 F1（P1-01）。"""
    if not predictions or len(predictions) != len(references):
        return 0.0

    total_f1 = 0.0
    for pred, ref in zip(predictions, references, strict=False):
        pred_tokens = set(_tokenize(pred))
        ref_tokens = set(_tokenize(ref))
        if not pred_tokens and not ref_tokens:
            total_f1 += 1.0
            continue
        if not pred_tokens or not ref_tokens:
            continue
        common = pred_tokens & ref_tokens
        if not common:
            continue
        precision = len(common) / len(pred_tokens)
        recall = len(common) / len(ref_tokens)
        if precision + recall > 0:
            total_f1 += 2 * precision * recall / (precision + recall)

    return total_f1 / len(predictions) if predictions else 0.0


def rouge_l(predictions: list[str], references: list[str]) -> float:
    """ROUGE-L：最长公共子序列 F1（P1-01）。"""
    if not predictions or len(predictions) != len(references):
        return 0.0

    total = 0.0
    for pred, ref in zip(predictions, references, strict=False):
        pred_tokens = _tokenize(pred)
        ref_tokens = _tokenize(ref)
        if not pred_tokens or not ref_tokens:
            continue

        lcs_len = _lcs_length(pred_tokens, ref_tokens)
        if lcs_len == 0:
            continue
        precision = lcs_len / len(pred_tokens)
        recall = lcs_len / len(ref_tokens)
        if precision + recall > 0:
            total += 2 * precision * recall / (precision + recall)

    return total / len(predictions) if predictions else 0.0


def _lcs_length(a: list[str], b: list[str]) -> int:
    """最长公共子序列长度。"""
    m, n = len(a), len(b)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if a[i - 1] == b[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    return dp[m][n]


def latency_p95(latencies_ms: list[float]) -> float:
    """P95 延迟。"""
    if not latencies_ms:
        return 0.0
    sorted_l = sorted(latencies_ms)
    idx = math.ceil(0.95 * len(sorted_l)) - 1
    return sorted_l[max(0, idx)]


def total_tokens(usage_list: list[dict[str, int]]) -> int:
    """总 Token 数。"""
    return sum(u.get("prompt_tokens", 0) + u.get("completion_tokens", 0) for u in usage_list)


def total_cost(costs: list[float]) -> float:
    """总成本（USD）。"""
    return sum(costs)


def tool_success_rate(results: list[bool]) -> float:
    """工具调用成功率（P1-05）。"""
    if not results:
        return 0.0
    return sum(1 for r in results if r) / len(results)


def compute_metrics(
    predictions: list[str],
    references: list[str],
    latencies_ms: list[float] | None = None,
    usage_list: list[dict[str, int]] | None = None,
    costs: list[float] | None = None,
    tool_results: list[bool] | None = None,
    metric_names: list[str] | None = None,
) -> dict[str, Any]:
    """计算全部指标（纯函数，可重算）。

    Args:
        predictions: 模型输出列表。
        references: 参考答案列表。
        latencies_ms: 各样本延迟。
        usage_list: 各样本 token 用量。
        costs: 各样本成本。
        tool_results: 工具调用成功/失败。
        metric_names: 指定计算的指标名（默认全量）。

    Returns:
        指标字典 {metric_name: value}。
    """
    names = metric_names or METRIC_NAMES
    results: dict[str, Any] = {}

    if "accuracy" in names:
        results["accuracy"] = round(accuracy(predictions, references), 4)
    if "f1" in names:
        results["f1"] = round(f1_score(predictions, references), 4)
    if "rouge_l" in names:
        results["rouge_l"] = round(rouge_l(predictions, references), 4)
    if "latency_p95_ms" in names:
        results["latency_p95_ms"] = round(latency_p95(latencies_ms or []), 2)
    if "tokens" in names:
        results["tokens"] = total_tokens(usage_list or [])
    if "cost_usd" in names:
        results["cost_usd"] = round(total_cost(costs or []), 6)
    if "tool_success_rate" in names:
        results["tool_success_rate"] = round(tool_success_rate(tool_results or []), 4)

    # 可选指标（BERTScore 降级为 None）
    if "bertscore" in names:
        results["bertscore"] = _try_bertscore(predictions, references)

    return results


def _try_bertscore(predictions: list[str], references: list[str]) -> float | None:
    """尝试计算 BERTScore，缺依赖时返回 None（降级）。"""
    try:
        from bert_score import score as bert_score_fn

        _p, _r, f1 = bert_score_fn(predictions, references, lang="zh", verbose=False)
        return round(float(f1.mean()), 4)
    except Exception:
        return None


def overall_score(metrics: dict[str, Any], weights: dict[str, float] | None = None) -> float:
    """加权总分（0~1）。

    仅使用归一化指标（accuracy/f1/rouge_l/bertscore/tool_success_rate），
    延迟/Token/成本为观测维度不参与评分。
    默认权重：accuracy 0.35, f1 0.25, rouge_l 0.25, tool_success_rate 0.15。
    """
    # 归一化指标（值域 0~1），非归一化指标不参与评分
    NORMALIZED_METRICS = {"accuracy", "f1", "rouge_l", "bertscore", "tool_success_rate"}

    default_weights = {
        "accuracy": 0.35,
        "f1": 0.25,
        "rouge_l": 0.25,
        "tool_success_rate": 0.15,
    }
    w = weights or default_weights

    total_w = 0.0
    total_v = 0.0
    for name, weight in w.items():
        if name not in NORMALIZED_METRICS:
            continue
        val = metrics.get(name)
        if val is not None and isinstance(val, (int, float)):
            total_v += val * weight
            total_w += weight

    # 未覆盖的归一化指标均分剩余权重
    remaining = [
        k for k in metrics
        if k not in w and k in NORMALIZED_METRICS and isinstance(metrics[k], (int, float))
    ]
    if remaining and total_w < 1.0:
        per = (1.0 - total_w) / len(remaining)
        for k in remaining:
            total_v += metrics[k] * per
            total_w += per

    return round(total_v / total_w if total_w > 0 else 0.0, 4)
