"""DeepSeek 适配器：OpenAI 兼容变体。"""

from __future__ import annotations

from .openai_adapter import OpenAIAdapter


class DeepSeekAdapter(OpenAIAdapter):
    """DeepSeek Chat API（OpenAI 兼容协议）。"""

    provider = "deepseek"
    protocol = "openai"
