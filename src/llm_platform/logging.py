"""结构化日志：trace_id 全链路透传 + 脱敏 processor + JSON 输出。

日志字段（全模块统一）：timestamp, level, event, trace_id, tenant_id,
project_id, api_key_id, model, provider, latency_ms, status, error_code, source
敏感字段由 utils/masking.py 统一处理，禁止散落手写脱敏。
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Any

import structlog

from .utils.masking import mask_dict

trace_id_var: ContextVar[str] = ContextVar("trace_id", default="-")


def set_trace_id(trace_id: str) -> None:
    """设置当前上下文的 trace_id。"""
    trace_id_var.set(trace_id)


def get_trace_id() -> str:
    """获取当前上下文的 trace_id。"""
    return trace_id_var.get()


def _add_trace_id(logger: Any, method: str, event_dict: dict) -> dict:  # noqa: ANN001, ARG001
    """structlog processor：注入 trace_id。"""
    event_dict["trace_id"] = get_trace_id()
    return event_dict


def _mask_sensitive(logger: Any, method: str, event_dict: dict) -> dict:  # noqa: ANN001, ARG001
    """structlog processor：脱敏敏感字段。"""
    return mask_dict(event_dict)


def configure_logging(debug: bool = False, level: str = "INFO", sample_rate: float = 1.0) -> None:
    """配置 structlog：JSON 渲染 + trace_id 注入 + 脱敏 + 采样率。

    Args:
        debug: 是否开启 DEBUG 级别。
        level: 日志级别字符串。
        sample_rate: 采样率 0.0~1.0（1.0 = 全量记录）。
    """
    log_level = logging.DEBUG if debug else getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=log_level)

    processors: list = [
        structlog.contextvars.merge_contextvars,
        _add_trace_id,
        _mask_sensitive,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    # 采样率 < 1.0 时过滤 debug/info 日志
    if sample_rate < 1.0:
        processors.insert(0, _sample_filter(sample_rate))

    if debug:
        processors.append(structlog.dev.ConsoleRenderer())
    else:
        processors.append(structlog.processors.JSONRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        cache_logger_on_first_use=True,
    )


def _sample_filter(rate: float):  # noqa: ANN202
    """返回一个采样 processor：按 rate 比例放行非 warning 日志。"""
    import random

    def processor(logger: Any, method: str, event_dict: dict) -> dict:  # noqa: ANN001, ARG001
        if method in ("warning", "error", "critical"):
            return event_dict
        if random.random() > rate:
            raise structlog.DropEvent
        return event_dict

    return processor


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """获取绑定到指定模块名的 logger。"""
    return structlog.get_logger(name)
