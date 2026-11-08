"""call_log 异步批量写入队列（ADR-006）。

每 500 条或 1s 触发 flush，executemany 批量写入。
队列容量 10000，溢出丢弃并计告警（保主链路）。
M3 增强：flush 失败时本地重试缓冲，指数退避，最终兜底丢弃+计数。
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..logging import get_logger
from ..monitoring.metrics import (
    record_calllog_dropped,
    record_calllog_flush_error,
    record_calllog_flushed,
)

logger = get_logger("gateway.buffer")


class CallLogBuffer:
    """call_log 异步批量写入缓冲（带重试）。"""

    def __init__(
        self,
        max_size: int = 10_000,
        flush_interval_s: float = 1.0,
        flush_batch: int = 500,
        max_retries: int = 3,
        retry_base_delay_s: float = 0.5,
    ) -> None:
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=max_size)
        self._flush_interval_s = flush_interval_s
        self._flush_batch = flush_batch
        self._max_retries = max_retries
        self._retry_base_delay_s = retry_base_delay_s
        self._flush_fn = None
        self._task: asyncio.Task | None = None
        self._running = False
        # 重试缓冲：flush 失败的批次暂存于此
        self._retry_buffer: list[dict[str, Any]] = []

    def set_flush_fn(self, fn) -> None:  # noqa: ANN001
        """设置批量写入函数（由 repositories 注入）。"""
        self._flush_fn = fn

    async def start(self) -> None:
        """启动后台 flush 协程。"""
        self._running = True
        self._task = asyncio.create_task(self._flush_loop())

    async def stop(self) -> None:
        """停止并 drain 队列（SIGTERM 时最多 5s）。"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except (TimeoutError, asyncio.CancelledError):
                pass
        # drain 剩余（含重试缓冲）
        await self._drain()

    def put(self, record: dict[str, Any]) -> None:
        """非阻塞入队，溢出时丢弃并计告警。"""
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            record_calllog_dropped()
            logger.warning("calllog_buffer_full_dropped")

    async def _flush_loop(self) -> None:
        """周期 flush 循环：每 flush_interval_s 或攒够 flush_batch 触发。"""
        while self._running:
            await asyncio.sleep(self._flush_interval_s)
            # 先处理重试缓冲
            if self._retry_buffer:
                retry_batch = self._retry_buffer[: self._flush_batch]
                self._retry_buffer = self._retry_buffer[self._flush_batch :]
                await self._flush_with_retry(retry_batch)
            # 再处理新到数据
            batch = self._collect_batch()
            if batch:
                await self._flush_with_retry(batch)

    def _collect_batch(self) -> list[dict[str, Any]]:
        batch = []
        while len(batch) < self._flush_batch:
            try:
                batch.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return batch

    async def _flush_with_retry(self, batch: list[dict[str, Any]]) -> None:
        """带指数退避的批量写入。失败后放入重试缓冲，超过上限则丢弃。"""
        if not self._flush_fn or not batch:
            return

        for attempt in range(self._max_retries):
            try:
                self._flush_fn(batch)
                record_calllog_flushed(len(batch))
                logger.debug("calllog_flushed", count=len(batch), attempt=attempt)
                return
            except Exception as exc:
                record_calllog_flush_error()
                delay = self._retry_base_delay_s * (2**attempt)
                logger.warning(
                    "calllog_flush_retry",
                    error=str(exc),
                    count=len(batch),
                    attempt=attempt + 1,
                    max_retries=self._max_retries,
                    next_delay_s=delay,
                )
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(delay)

        # 所有重试均失败：放入重试缓冲（下次 flush 循环再试），或丢弃
        if len(self._retry_buffer) < 10_000:
            self._retry_buffer.extend(batch)
            logger.warning("calllog_batch_moved_to_retry_buffer", count=len(batch))
        else:
            record_calllog_dropped()
            logger.error("calllog_batch_dropped_after_retries", count=len(batch))

    async def _drain(self) -> None:
        """drain 队列中剩余记录（含重试缓冲）。"""
        batch = self._collect_batch()
        batch.extend(self._retry_buffer)
        self._retry_buffer.clear()
        if batch:
            # drain 时只试一次（进程即将退出）
            if self._flush_fn:
                try:
                    self._flush_fn(batch)
                    record_calllog_flushed(len(batch))
                except Exception as exc:
                    record_calllog_flush_error()
                    logger.error("calllog_drain_failed", error=str(exc), count=len(batch))
