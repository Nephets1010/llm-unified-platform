"""适配器测试：请求翻译（mock 上游，不打真实外网）。"""

from __future__ import annotations

import pytest

from llm_platform.adapters.anthropic_adapter import AnthropicAdapter
from llm_platform.adapters.base import ChatMessage, ChatRequest, Usage
from llm_platform.adapters.deepseek_adapter import DeepSeekAdapter
from llm_platform.adapters.ollama_adapter import OllamaAdapter
from llm_platform.adapters.openai_adapter import OpenAIAdapter
from llm_platform.adapters.qwen_adapter import QwenAdapter
from llm_platform.adapters.registry import get_adapter_registry


def _make_request() -> ChatRequest:
    return ChatRequest(
        model="test-model",
        messages=[
            ChatMessage(role="system", content="你是助手"),
            ChatMessage(role="user", content="你好"),
        ],
        temperature=0.7,
        max_tokens=100,
    )


class TestAdapterRegistry:
    def test_all_providers_registered(self):
        registry = get_adapter_registry()
        providers = registry.list_providers()
        assert "openai" in providers
        assert "deepseek" in providers
        assert "qwen" in providers
        assert "anthropic" in providers
        assert "ollama" in providers

    def test_get_adapter(self):
        registry = get_adapter_registry()
        assert isinstance(registry.get("openai"), OpenAIAdapter)
        assert isinstance(registry.get("deepseek"), DeepSeekAdapter)
        assert isinstance(registry.get("qwen"), QwenAdapter)

    def test_get_by_protocol(self):
        registry = get_adapter_registry()
        assert registry.get_by_protocol("openai") is not None
        assert registry.get_by_protocol("anthropic") is not None
        assert registry.get_by_protocol("ollama") is not None


class TestOpenAIAdapter:
    @pytest.mark.asyncio
    async def test_chat_request_translation(self):
        """验证请求翻译为 OpenAI 格式。"""
        adapter = OpenAIAdapter()
        request = _make_request()
        payload = adapter._build_payload(request, stream=False)
        assert payload["model"] == "test-model"
        assert payload["stream"] is False
        assert len(payload["messages"]) == 2
        assert payload["temperature"] == 0.7

    @pytest.mark.asyncio
    async def test_chat_stream_payload(self):
        adapter = OpenAIAdapter()
        request = _make_request()
        payload = adapter._build_payload(request, stream=True)
        assert payload["stream"] is True


class TestDeepSeekAdapter:
    def test_provider(self):
        assert DeepSeekAdapter().provider == "deepseek"
        assert DeepSeekAdapter().protocol == "openai"


class TestQwenAdapter:
    def test_provider(self):
        assert QwenAdapter().provider == "qwen"
        assert QwenAdapter().protocol == "openai"


class TestAnthropicAdapter:
    def test_split_messages(self):
        adapter = AnthropicAdapter()
        request = _make_request()
        system, dialog = adapter._split_messages(request)
        assert system == "你是助手"
        assert len(dialog) == 1  # 只有 user
        assert dialog[0]["role"] == "user"

    def test_provider(self):
        assert AnthropicAdapter().provider == "anthropic"
        assert AnthropicAdapter().protocol == "anthropic"


class TestOllamaAdapter:
    def test_provider(self):
        assert OllamaAdapter().provider == "ollama"
        assert OllamaAdapter().protocol == "ollama"


class TestUsage:
    def test_total_tokens(self):
        usage = Usage(prompt_tokens=10, completion_tokens=20)
        assert usage.total_tokens == 30

    def test_estimated_flag(self):
        usage = Usage(prompt_tokens=10, completion_tokens=20, tokens_estimated=True)
        assert usage.tokens_estimated is True
