"""批量推理执行器（P1-06）：独立 eval: 限流桶，不占在线配额。

评测推理走 eval:rl: 独立桶，任务内并发受 config.concurrency 限制。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from ..adapters.base import ChatMessage, ChatRequest
from ..adapters.registry import get_adapter_registry
from ..config import get_settings
from ..logging import get_logger
from ..ratelimit.token_bucket import TokenBucket
from .datasets import DatasetManager
from .metrics import compute_metrics, overall_score

logger = get_logger("eval.runner")


class EvalRunner:
    """评测批量推理执行器。

    使用独立 eval: 限流桶，复用 adapters，不走 gateway 主链路。
    """

    def __init__(self, db, redis_client=None) -> None:  # noqa: ANN001
        self.db = db
        self.settings = get_settings()
        # 评测独立桶（eval:rl: 前缀，不与在线桶混用，ADR-003）
        self.eval_bucket = TokenBucket(redis_client=redis_client, prefix="eval:rl")
        self.dataset_mgr = DatasetManager(db)

    async def run_task(
        self,
        task_id: int,
        dataset_id: int,
        models: list[str],
        config: dict[str, Any] | None = None,
        progress_cb=None,  # noqa: ANN001
    ) -> list[dict[str, Any]]:
        """执行评测任务：对每个模型跑全量样本，计算指标。

        Args:
            task_id: 任务 ID。
            dataset_id: 基准集 ID。
            models: 待评模型列表。
            config: 任务配置（temperature/metrics/concurrency）。
            progress_cb: 进度回调 (progress: float) -> None。

        Returns:
            每模型的评测结果列表。
        """
        cfg = config or {}
        temperature = cfg.get("temperature", 0.0)
        metric_names = cfg.get("metrics")
        concurrency = cfg.get("concurrency", 4)

        items = self.dataset_mgr.get_items(dataset_id)
        if not items:
            logger.warning("eval_empty_dataset", dataset_id=dataset_id)
            return []

        # ③ checksum 自动校验：运行前验证数据完整性
        self._verify_dataset_checksum(dataset_id, items)

        # 为每个模型并发执行
        semaphore = asyncio.Semaphore(concurrency)
        total_work = len(models) * len(items)
        completed = 0

        async def eval_single_item(model: str, item: dict) -> dict[str, Any]:
            nonlocal completed
            async with semaphore:
                # 评测限流（eval: 独立桶）
                self.eval_bucket.check(
                    "model", model,
                    self.settings.rate_limit_default_rpm * 10,
                    self.settings.rate_limit_default_tpm * 10,
                )
                result = await self._call_model(model, item, temperature)
                completed += 1
                if progress_cb:
                    progress_cb(completed / total_work)
                return result

        # 并发执行所有 (model, item) 组合
        all_tasks = []
        for model in models:
            for item in items:
                all_tasks.append(eval_single_item(model, item))

        raw_results = await asyncio.gather(*all_tasks, return_exceptions=True)

        # 按模型聚合
        model_results: dict[str, list[dict]] = {m: [] for m in models}
        idx = 0
        for model in models:
            for _ in items:
                r = raw_results[idx]
                if isinstance(r, Exception):
                    logger.error("eval_item_failed", model=model, error=str(r))
                    model_results[model].append({
                        "prediction": "",
                        "reference": "",
                        "latency_ms": 0,
                        "usage": {"prompt_tokens": 0, "completion_tokens": 0},
                        "cost": 0.0,
                        "error": str(r),
                    })
                else:
                    model_results[model].append(r)
                idx += 1

        # 计算各模型指标
        reports = []
        for model in models:
            results = model_results[model]
            predictions = [r["prediction"] for r in results]
            references = [r["reference"] for r in results]
            latencies = [r["latency_ms"] for r in results]
            usages = [r["usage"] for r in results]
            costs = [r["cost"] for r in results]

            metrics = compute_metrics(
                predictions, references,
                latencies_ms=latencies,
                usage_list=usages,
                costs=costs,
                metric_names=metric_names,
            )
            score = overall_score(metrics)

            reports.append({
                "model": model,
                "overall_score": score,
                "metrics": metrics,
                "sample_count": len(results),
            })

        return reports

    def _verify_dataset_checksum(self, dataset_id: int, items: list[dict]) -> None:
        """③ checksum 自动校验：运行前验证数据完整性，篡改必须拒绝（fail-closed）。

        Raises:
            ValidationError: checksum 不匹配（数据被篡改或规范化不一致）。
        """
        from ..errors import ValidationError
        from .datasets import compute_checksum

        from ..db.repositories.eval_repo import EvalRepo

        repo = EvalRepo(self.db)
        dataset = repo.get_dataset(dataset_id)
        if dataset and dataset.checksum:
            actual = compute_checksum(items)
            if actual != dataset.checksum:
                logger.error(
                    "eval_checksum_mismatch",
                    dataset_id=dataset_id,
                    expected=dataset.checksum[:16],
                    actual=actual[:16],
                )
                raise ValidationError(
                    message=f"基准集 checksum 校验失败（数据可能被篡改）: dataset_id={dataset_id}",
                    dataset_id=dataset_id,
                    expected_checksum=dataset.checksum[:16],
                    actual_checksum=actual[:16],
                )

    async def _call_model(
        self, model: str, item: dict[str, Any], temperature: float
    ) -> dict[str, Any]:
        """调用单个样本推理。"""
        from ..db.repositories.endpoint_repo import EndpointRepo

        endpoint_repo = EndpointRepo(self.db)
        candidates = endpoint_repo.list_by_model(model)
        if not candidates:
            raise ValueError(f"模型 {model} 无可用端点")

        endpoint = candidates[0]
        adapter_registry = get_adapter_registry()
        adapter = adapter_registry.get(endpoint.provider) or adapter_registry.get_by_protocol(endpoint.protocol)
        if adapter is None:
            raise ValueError(f"无适配器: {endpoint.provider}")

        import os
        api_key = os.environ.get(endpoint.credential_env, "")

        request = ChatRequest(
            model=model,
            messages=[ChatMessage(role="user", content=item["input_prompt"])],
            temperature=temperature,
            stream=False,
        )

        start = time.perf_counter()
        try:
            response = await adapter.chat(request, base_url=endpoint.base_url, api_key=api_key)
            latency_ms = int((time.perf_counter() - start) * 1000)
            prediction = response.choices[0].message.content if response.choices else ""
            usage = response.usage
            cost = (
                usage.prompt_tokens / 1000.0 * endpoint.cost_per_1k_prompt
                + usage.completion_tokens / 1000.0 * endpoint.cost_per_1k_completion
            )
            return {
                "prediction": prediction,
                "reference": item.get("expected_output", ""),
                "latency_ms": latency_ms,
                "usage": {
                    "prompt_tokens": usage.prompt_tokens,
                    "completion_tokens": usage.completion_tokens,
                },
                "cost": cost,
                "error": None,
            }
        except Exception as exc:
            latency_ms = int((time.perf_counter() - start) * 1000)
            return {
                "prediction": "",
                "reference": item.get("expected_output", ""),
                "latency_ms": latency_ms,
                "usage": {"prompt_tokens": 0, "completion_tokens": 0},
                "cost": 0.0,
                "error": str(exc),
            }
