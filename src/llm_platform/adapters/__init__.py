"""适配器包：多厂商协议归一化。"""

from .base import BaseAdapter, ChatChoice, ChatMessage, ChatRequest, ChatResponse, Usage
from .registry import AdapterRegistry, get_adapter_registry

__all__ = [
    "BaseAdapter",
    "ChatChoice",
    "ChatMessage",
    "ChatRequest",
    "ChatResponse",
    "Usage",
    "AdapterRegistry",
    "get_adapter_registry",
]
