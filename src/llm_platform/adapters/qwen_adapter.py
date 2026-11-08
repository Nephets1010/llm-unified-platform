"""Qwen 适配器：OpenAI 兼容变体（DashScope compatible-mode）。"""

from __future__ import annotations

from .openai_adapter import OpenAIAdapter


class QwenAdapter(OpenAIAdapter):
    """通义千问 DashScope API（OpenAI 兼容协议）。"""

    provider = "qwen"
    protocol = "openai"
