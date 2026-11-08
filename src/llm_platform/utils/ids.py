"""ID 生成工具：trace_id / request_id 均为 UUIDv4。"""

from __future__ import annotations

import uuid


def generate_trace_id() -> str:
    """生成全链路追踪 ID（UUIDv4 格式）。"""
    return str(uuid.uuid4())


def generate_request_id() -> str:
    """生成请求 ID（与 trace_id 同格式，用于管理面审计）。"""
    return str(uuid.uuid4())
