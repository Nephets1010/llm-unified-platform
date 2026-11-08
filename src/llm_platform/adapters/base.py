"""适配器基类与统一请求/响应模型（协议归一化核心，ADR-001）。

所有厂商协议在适配器内双向翻译；对外只暴露 OpenAI Chat Completions 形状。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """统一消息模型。"""

    role: str  # system / user / assistant
    content: str


class ChatRequest(BaseModel):
    """统一请求模型。"""

    model: str
    messages: list[ChatMessage]
    temperature: float = 0.7
    max_tokens: int | None = None
    stream: bool = False
    user: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatChoice(BaseModel):
    """统一选项模型。"""

    index: int = 0
    message: ChatMessage
    finish_reason: str = "stop"


class Usage(BaseModel):
    """统一计量模型。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    tokens_estimated: bool = False

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class ChatResponse(BaseModel):
    """统一响应模型。"""

    id: str
    model: str
    choices: list[ChatChoice]
    usage: Usage = Field(default_factory=Usage)
    provider_request_id: str | None = None
    latency_ms: int = 0


class StreamChunk(BaseModel):
    """流式增量块（SSE data 载荷）。"""

    id: str
    object: str = "chat.completion.chunk"
    created: int = 0
    model: str = ""
    choices: list[dict[str, Any]] = Field(default_factory=list)
    usage: Usage | None = None


class BaseAdapter(ABC):
    """厂商适配器：统一入参出参，隔离厂商 SDK/协议差异。

    新增厂商 = 新增一个 BaseAdapter 子类 + 注册（ADR-001）。
    """

    provider: str = "base"
    protocol: str = "openai"  # openai / anthropic / ollama

    @abstractmethod
    async def chat(self, request: ChatRequest, *, base_url: str, api_key: str) -> ChatResponse:
        """非流式对话。

        Args:
            request: 统一请求。
            base_url: 厂商 API base URL。
            api_key: 厂商密钥（从 credential_env 运行时读取）。

        Returns:
            统一响应。
        """
        raise NotImplementedError

    @abstractmethod
    def chat_stream(
        self, request: ChatRequest, *, base_url: str, api_key: str
    ) -> AsyncIterator[StreamChunk]:
        """流式对话：yield StreamChunk（OpenAI delta 约定）。

        实现方须在最后一个 chunk 中携带 usage（或标记 estimated）。
        """
        raise NotImplementedError

    async def embeddings(
        self, model: str, input_text: str | list[str], *, base_url: str, api_key: str
    ) -> dict[str, Any]:
        """Embedding 调用（默认不支持，子类可覆盖）。"""
        raise NotImplementedError(f"{self.provider} 不支持 embeddings")
