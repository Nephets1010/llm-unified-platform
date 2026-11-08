"""适配器注册表：provider → 适配器实例映射。"""

from __future__ import annotations

from ..logging import get_logger
from .anthropic_adapter import AnthropicAdapter
from .base import BaseAdapter
from .deepseek_adapter import DeepSeekAdapter
from .ollama_adapter import OllamaAdapter
from .openai_adapter import OpenAIAdapter
from .qwen_adapter import QwenAdapter

logger = get_logger("adapter.registry")


class AdapterRegistry:
    """provider → 适配器实例注册表。"""

    def __init__(self) -> None:
        self._adapters: dict[str, BaseAdapter] = {}

    def register(self, adapter: BaseAdapter) -> None:
        self._adapters[adapter.provider] = adapter
        logger.info("adapter_registered", provider=adapter.provider)

    def get(self, provider: str) -> BaseAdapter | None:
        return self._adapters.get(provider)

    def get_by_protocol(self, protocol: str) -> BaseAdapter | None:
        """按协议类型查找适配器（openai/anthropic/ollama）。"""
        for adapter in self._adapters.values():
            if adapter.protocol == protocol:
                return adapter
        return None

    def list_providers(self) -> list[str]:
        return list(self._adapters.keys())


_registry: AdapterRegistry | None = None


def get_adapter_registry() -> AdapterRegistry:
    """获取全局适配器注册表（懒加载，注册全部内置适配器）。"""
    global _registry
    if _registry is None:
        _registry = AdapterRegistry()
        _registry.register(OpenAIAdapter())
        _registry.register(DeepSeekAdapter())
        _registry.register(QwenAdapter())
        _registry.register(AnthropicAdapter())
        _registry.register(OllamaAdapter())
    return _registry
