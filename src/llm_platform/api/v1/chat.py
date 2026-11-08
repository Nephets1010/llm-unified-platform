"""POST /v1/chat/completions — OpenAI 兼容，支持流式 SSE 与非流式。"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends

from ...adapters.base import ChatMessage, ChatRequest
from ...auth.service import AuthContext
from ...deps import DbSession, RedisClient, get_current_auth
from ...gateway.service import GatewayService
from ...gateway.sse import sse_response
from ...logging import get_logger, get_trace_id
from ...models.schemas import (
    ChatChoiceOut,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessageIn,
    UsageOut,
)

logger = get_logger("api.v1.chat")
router = APIRouter(prefix="/v1", tags=["chat"])


@router.post("/chat/completions")
async def chat_completions(
    body: ChatCompletionRequest,
    db: DbSession,
    redis: RedisClient,
    auth: AuthContext = Depends(get_current_auth),
):
    """OpenAI 兼容 Chat Completions 接口。

    - 非流式：返回 OpenAI 形状 JSON（含 x_trace_id）
    - 流式（stream=true）：返回 text/event-stream
    """
    # 构造统一请求
    request = ChatRequest(
        model=body.model,
        messages=[ChatMessage(role=m.role, content=m.content) for m in body.messages],
        temperature=body.temperature,
        max_tokens=body.max_tokens,
        stream=body.stream,
        user=body.user,
    )

    gateway = GatewayService(db=db, redis_client=redis)
    trace_id = get_trace_id()

    if body.stream:
        stream_gen, endpoint, _ = await gateway.chat_completions_stream(request, auth)
        response = sse_response(stream_gen)
        response.headers["X-Request-Id"] = trace_id
        return response

    # 非流式
    result = await gateway.chat_completions(request, auth)

    # 转换为 OpenAI 形状
    return ChatCompletionResponse(
        id=result.id,
        created=int(time.time()),
        model=result.model,
        choices=[
            ChatChoiceOut(
                index=c.index,
                message=ChatMessageIn(role=c.message.role, content=c.message.content),
                finish_reason=c.finish_reason,
            )
            for c in result.choices
        ],
        usage=UsageOut(
            prompt_tokens=result.usage.prompt_tokens,
            completion_tokens=result.usage.completion_tokens,
            total_tokens=result.usage.total_tokens,
        ),
        x_trace_id=trace_id,
    )
