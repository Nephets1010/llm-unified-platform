"""SSE 流式转发：边收边发，OpenAI chunk 格式。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from starlette.responses import StreamingResponse

from ..adapters.base import StreamChunk


async def sse_generator(chunks: AsyncIterator[StreamChunk]) -> AsyncIterator[str]:
    """将 StreamChunk 异步迭代器转为 SSE 文本流。

    输出格式：
        data: {...}\\n\\n
        data: [DONE]\\n\\n
    """
    async for chunk in chunks:
        payload = {
            "id": chunk.id,
            "object": chunk.object,
            "created": chunk.created,
            "model": chunk.model,
            "choices": chunk.choices,
        }
        if chunk.usage:
            payload["usage"] = {
                "prompt_tokens": chunk.usage.prompt_tokens,
                "completion_tokens": chunk.usage.completion_tokens,
                "total_tokens": chunk.usage.total_tokens,
            }
        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    yield "data: [DONE]\n\n"


def sse_response(chunks: AsyncIterator[StreamChunk]) -> StreamingResponse:
    """构造 SSE StreamingResponse。"""
    return StreamingResponse(
        sse_generator(chunks),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
