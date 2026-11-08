"""OpenAI 兼容适配器（覆盖 OpenAI / vLLM / 大多数兼容网关）。

连接池复用：共享 httpx.AsyncClient，流式 usage 估算。
"""

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

logger = get_logger("adapter.openai")


class OpenAIAdapter(BaseAdapter):
    """OpenAI Chat Completions 兼容适配器。"""

    provider = "openai"
    protocol = "openai"

    def __init__(self, timeout_s: float = 60.0) -> None:
        self.timeout_s = timeout_s
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout_s)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def _build_payload(self, request: ChatRequest, stream: bool) -> dict:
        payload: dict = {
            "model": request.model,
            "messages": [m.model_dump() for m in request.messages],
            "temperature": request.temperature,
            "stream": stream,
        }
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        return payload

    async def chat(self, request: ChatRequest, *, base_url: str, api_key: str) -> ChatResponse:
        payload = self._build_payload(request, stream=False)
        start = time.perf_counter()
        try:
            client = await self._get_client()
            resp = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            raise UpstreamError(message=f"OpenAI 兼容接口调用失败: {exc}") from exc

        latency = int((time.perf_counter() - start) * 1000)
        choice = data["choices"][0]
        usage_raw = data.get("usage", {})
        return ChatResponse(
            id=data.get("id", f"chatcmpl-{uuid.uuid4().hex[:12]}"),
            model=data.get("model", request.model),
            choices=[
                ChatChoice(
                    index=choice.get("index", 0),
                    message=ChatMessage(**choice["message"]),
                    finish_reason=choice.get("finish_reason", "stop"),
                )
            ],
            usage=Usage(
                prompt_tokens=usage_raw.get("prompt_tokens", 0),
                completion_tokens=usage_raw.get("completion_tokens", 0),
            ),
            provider_request_id=data.get("id"),
            latency_ms=latency,
        )

    async def chat_stream(
        self, request: ChatRequest, *, base_url: str, api_key: str
    ) -> AsyncIterator[StreamChunk]:
        payload = self._build_payload(request, stream=True)
        created = int(time.time())
        chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        try:
            client = await self._get_client()
            async with client.stream(
                "POST",
                f"{base_url.rstrip('/')}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[len("data:"):].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    # 转发上游 usage（如有）
                    usage = None
                    if "usage" in data and data["usage"]:
                        u = data["usage"]
                        usage = Usage(
                            prompt_tokens=u.get("prompt_tokens", 0),
                            completion_tokens=u.get("completion_tokens", 0),
                        )

                    for choice in data.get("choices", []):
                        delta = choice.get("delta", {})
                        yield StreamChunk(
                            id=data.get("id", chunk_id),
                            created=data.get("created", created),
                            model=data.get("model", request.model),
                            choices=[
                                {
                                    "index": choice.get("index", 0),
                                    "delta": delta,
                                    "finish_reason": choice.get("finish_reason"),
                                }
                            ],
                            usage=usage,
                        )
        except httpx.HTTPError as exc:
            raise UpstreamError(message=f"流式调用失败: {exc}") from exc

    async def embeddings(
        self, model: str, input_text: str | list[str], *, base_url: str, api_key: str
    ) -> dict:
        payload = {"model": model, "input": input_text}
        try:
            client = await self._get_client()
            resp = await client.post(
                f"{base_url.rstrip('/')}/embeddings",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            raise UpstreamError(message=f"Embeddings 调用失败: {exc}") from exc
