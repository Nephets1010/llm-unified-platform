"""Ollama 本地模型适配器（自托管兜底）。"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator

import httpx

from ..errors import UpstreamError
from ..logging import get_logger
from .base import (
    BaseAdapter,
    ChatChoice,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    StreamChunk,
    Usage,
)

logger = get_logger("adapter.ollama")


class OllamaAdapter(BaseAdapter):
    """Ollama /api/chat 适配器。"""

    provider = "ollama"
    protocol = "ollama"

    def __init__(self, timeout_s: float = 120.0) -> None:
        self.timeout_s = timeout_s
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout_s)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def chat(self, request: ChatRequest, *, base_url: str, api_key: str) -> ChatResponse:
        payload = {
            "model": request.model,
            "messages": [m.model_dump() for m in request.messages],
            "stream": False,
        }
        start = time.perf_counter()
        try:
            client = await self._get_client()
            resp = await client.post(f"{base_url.rstrip('/')}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            raise UpstreamError(message=f"Ollama 调用失败: {exc}") from exc

        latency = int((time.perf_counter() - start) * 1000)
        msg = data.get("message", {})
        return ChatResponse(
            id=f"ollama-{uuid.uuid4().hex[:12]}",
            model=data.get("model", request.model),
            choices=[
                ChatChoice(
                    message=ChatMessage(role=msg.get("role", "assistant"), content=msg.get("content", "")),
                    finish_reason=data.get("done_reason", "stop"),
                )
            ],
            usage=Usage(
                prompt_tokens=data.get("prompt_eval_count", 0),
                completion_tokens=data.get("eval_count", 0),
            ),
            latency_ms=latency,
        )

    async def chat_stream(
        self, request: ChatRequest, *, base_url: str, api_key: str
    ) -> AsyncIterator[StreamChunk]:
        payload = {
            "model": request.model,
            "messages": [m.model_dump() for m in request.messages],
            "stream": True,
        }
        created = int(time.time())
        chunk_id = f"ollama-{uuid.uuid4().hex[:12]}"
        try:
            client = await self._get_client()
            async with client.stream(
                "POST", f"{base_url.rstrip('/')}/api/chat", json=payload
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    content = data.get("message", {}).get("content", "")
                    if content:
                        yield StreamChunk(
                            id=chunk_id, created=created, model=request.model,
                            choices=[{"index": 0, "delta": {"content": content}, "finish_reason": None}],
                        )

                    if data.get("done"):
                        usage = Usage(
                            prompt_tokens=data.get("prompt_eval_count", 0),
                            completion_tokens=data.get("eval_count", 0),
                            tokens_estimated=True,
                        )
                        yield StreamChunk(
                            id=chunk_id, created=created, model=request.model,
                            choices=[{"index": 0, "delta": {}, "finish_reason": "stop"}],
                            usage=usage,
                        )
        except httpx.HTTPError as exc:
            raise UpstreamError(message=f"Ollama 流式调用失败: {exc}") from exc
