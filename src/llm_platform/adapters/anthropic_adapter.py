"""Anthropic Claude 适配器：统一模型 → Messages API。

M3 增强：真流式 SSE（stream=true + event 解析），替代 M1 的假流式。
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

logger = get_logger("adapter.anthropic")


class AnthropicAdapter(BaseAdapter):
    """Anthropic Messages API 适配器。"""

    provider = "anthropic"
    protocol = "anthropic"

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

    def _split_messages(self, request: ChatRequest) -> tuple[str | None, list[dict]]:
        system_parts = [m.content for m in request.messages if m.role == "system"]
        dialog = [
            {"role": m.role, "content": m.content}
            for m in request.messages
            if m.role in ("user", "assistant")
        ]
        system = "\n".join(system_parts) if system_parts else None
        return system, dialog

    def _build_payload(self, request: ChatRequest, stream: bool = False) -> dict:
        system, dialog = self._split_messages(request)
        payload: dict = {
            "model": request.model,
            "messages": dialog,
            "max_tokens": request.max_tokens or 1024,
        }
        if system:
            payload["system"] = system
        if stream:
            payload["stream"] = True
        return payload

    async def chat(self, request: ChatRequest, *, base_url: str, api_key: str) -> ChatResponse:
        payload = self._build_payload(request, stream=False)

        start = time.perf_counter()
        try:
            client = await self._get_client()
            resp = await client.post(
                f"{base_url.rstrip('/')}/v1/messages",
                json=payload,
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            raise UpstreamError(message=f"Anthropic 接口调用失败: {exc}") from exc

        latency = int((time.perf_counter() - start) * 1000)
        content_blocks = data.get("content", [])
        text = "".join(b.get("text", "") for b in content_blocks if b.get("type") == "text")
        usage_raw = data.get("usage", {})
        return ChatResponse(
            id=data.get("id", f"msg-{uuid.uuid4().hex[:12]}"),
            model=data.get("model", request.model),
            choices=[
                ChatChoice(
                    message=ChatMessage(role="assistant", content=text),
                    finish_reason=data.get("stop_reason", "stop"),
                )
            ],
            usage=Usage(
                prompt_tokens=usage_raw.get("input_tokens", 0),
                completion_tokens=usage_raw.get("output_tokens", 0),
            ),
            provider_request_id=data.get("id"),
            latency_ms=latency,
        )

    async def chat_stream(
        self, request: ChatRequest, *, base_url: str, api_key: str
    ) -> AsyncIterator[StreamChunk]:
        """真流式 SSE：调用 Anthropic Messages API stream=true，解析事件流。"""
        payload = self._build_payload(request, stream=True)
        created = int(time.time())
        chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

        # 累计 usage
        input_tokens = 0
        output_tokens = 0
        text_parts: list[str] = []

        try:
            client = await self._get_client()
            async with client.stream(
                "POST",
                f"{base_url.rstrip('/')}/v1/messages",
                json=payload,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "accept": "text/event-stream",
                },
            ) as resp:
                resp.raise_for_status()

                # 先发 role delta
                yield StreamChunk(
                    id=chunk_id,
                    created=created,
                    model=request.model,
                    choices=[{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
                )

                event_type = ""
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if line.startswith("event:"):
                        event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        data_str = line[5:].strip()
                        if not data_str:
                            continue
                        try:
                            data = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        # message_start：提取 input_tokens
                        if event_type == "message_start":
                            msg = data.get("message", {})
                            usage_raw = msg.get("usage", {})
                            input_tokens = usage_raw.get("input_tokens", 0)

                        # content_block_delta：文本增量
                        elif event_type == "content_block_delta":
                            delta = data.get("delta", {})
                            if delta.get("type") == "text_delta":
                                text = delta.get("text", "")
                                if text:
                                    text_parts.append(text)
                                    yield StreamChunk(
                                        id=chunk_id,
                                        created=created,
                                        model=request.model,
                                        choices=[{"index": 0, "delta": {"content": text}, "finish_reason": None}],
                                    )

                        # message_delta：stop_reason + output_tokens
                        elif event_type == "message_delta":
                            delta = data.get("delta", {})
                            usage_raw = data.get("usage", {})
                            output_tokens = usage_raw.get("output_tokens", output_tokens)
                            stop_reason = delta.get("stop_reason", "stop")
                            yield StreamChunk(
                                id=chunk_id,
                                created=created,
                                model=request.model,
                                choices=[{"index": 0, "delta": {}, "finish_reason": stop_reason}],
                                usage=Usage(
                                    prompt_tokens=input_tokens,
                                    completion_tokens=output_tokens,
                                ),
                            )

                        # message_stop / error：结束
                        elif event_type == "error":
                            err = data.get("error", {})
                            raise UpstreamError(
                                message=f"Anthropic 流式错误: {err.get('message', 'unknown')}"
                            )

        except httpx.HTTPError as exc:
            raise UpstreamError(message=f"Anthropic 流式调用失败: {exc}") from exc

        # 兜底：如果没收到 message_delta，发一个收尾 chunk
        if not text_parts and input_tokens == 0:
            yield StreamChunk(
                id=chunk_id,
                created=created,
                model=request.model,
                choices=[{"index": 0, "delta": {}, "finish_reason": "stop"}],
                usage=Usage(prompt_tokens=0, completion_tokens=0),
            )
