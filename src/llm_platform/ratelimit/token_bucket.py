"""三级令牌桶限流（RPM+TPM）：Redis Lua 原子扣减 + 本地降级。

在线桶前缀 rl:；评测桶前缀 eval:rl:（不得混用，ADR-003）。
Redis 不可用时降级本地桶，不得放行失控。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..errors import RateLimitError
from ..logging import get_logger

logger = get_logger("ratelimit.bucket")

# ── Redis Lua 脚本：原子令牌桶扣减 ──
LUA_TOKEN_BUCKET = """
local key = KEYS[1]
local max_tokens = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])

local bucket = redis.call('HMGET', key, 'tokens', 'last_refill')
local tokens = tonumber(bucket[1]) or max_tokens
local last_refill = tonumber(bucket[2]) or now

-- 补充令牌
local elapsed = math.max(0, now - last_refill)
tokens = math.min(max_tokens, tokens + elapsed * refill_rate)

local allowed = 0
local remaining = tokens
if tokens >= requested then
    tokens = tokens - requested
    allowed = 1
    remaining = tokens
end

redis.call('HMSET', key, 'tokens', tokens, 'last_refill', now)
redis.call('EXPIRE', key, 60)

return {allowed, remaining}
"""


@dataclass
class RateLimitResult:
    """限流检查结果。"""

    allowed: bool
    remaining: float
    retry_after: float = 0.0
    limit: float = 0.0
    dimension: str = ""


@dataclass
class LocalTokenBucket:
    """本地内存令牌桶（Redis 降级兜底）。"""

    max_tokens: float
    refill_rate: float  # tokens per second
    tokens: float = field(default=0.0)
    last_refill: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        self.tokens = self.max_tokens

    def try_consume(self, amount: float = 1.0) -> RateLimitResult:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.max_tokens, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now

        if self.tokens >= amount:
            self.tokens -= amount
            return RateLimitResult(allowed=True, remaining=self.tokens, limit=self.max_tokens)
        return RateLimitResult(
            allowed=False,
            remaining=self.tokens,
            retry_after=(amount - self.tokens) / self.refill_rate if self.refill_rate > 0 else 60,
            limit=self.max_tokens,
        )


class TokenBucket:
    """三级令牌桶限流器（tenant / key / model）。

    Redis Lua 原子扣减，不可用时降级本地桶（不得放行失控）。
    """

    def __init__(self, redis_client=None, prefix: str = "rl") -> None:
        self.redis = redis_client
        self.prefix = prefix
        self._local_buckets: dict[str, LocalTokenBucket] = {}
        self._use_local: bool = False

    def _bucket_key(self, dimension: str, subject_id: str) -> str:
        return f"{self.prefix}:{dimension}:{subject_id}"

    def check(
        self,
        dimension: str,
        subject_id: str,
        rpm_limit: int,
        tpm_limit: int,
        token_cost: int = 0,
    ) -> RateLimitResult:
        """检查 RPM + TPM 双维度限流。

        Args:
            dimension: tenant / key / model。
            subject_id: 主体 ID。
            rpm_limit: 每分钟请求数限制。
            tpm_limit: 每分钟 Token 数限制。
            token_cost: 本次请求预计 Token 消耗（TPM 扣减）。

        Returns:
            RateLimitResult

        Raises:
            RateLimitError: 超限时抛出。
        """
        # RPM 检查
        rpm_result = self._check_single(f"{dimension}:rpm:{subject_id}", rpm_limit, rpm_limit / 60.0, 1)
        if not rpm_result.allowed:
            raise RateLimitError(
                message=f"RPM 超限（{dimension}）",
                dimension=dimension,
                limit=rpm_limit,
                retry_after=int(rpm_result.retry_after) + 1,
            )

        # TPM 检查（token_cost > 0 时）
        if token_cost > 0:
            tpm_result = self._check_single(
                f"{dimension}:tpm:{subject_id}", tpm_limit, tpm_limit / 60.0, token_cost
            )
            if not tpm_result.allowed:
                raise RateLimitError(
                    message=f"TPM 超限（{dimension}）",
                    dimension=dimension,
                    limit=tpm_limit,
                    retry_after=int(tpm_result.retry_after) + 1,
                )

        return RateLimitResult(
            allowed=True, remaining=rpm_result.remaining, limit=rpm_limit, dimension=dimension
        )

    def _check_single(
        self, key_suffix: str, max_tokens: float, refill_rate: float, amount: float
    ) -> RateLimitResult:
        """单维度令牌桶检查（Redis Lua 或本地降级）。"""
        key = f"{self.prefix}:{key_suffix}"

        if self.redis and not self._use_local:
            try:
                result = self.redis.eval(  # type: ignore[union-attr]
                    LUA_TOKEN_BUCKET,
                    1,
                    key,
                    str(max_tokens),
                    str(refill_rate),
                    str(time.time()),
                    str(amount),
                )
                allowed = bool(result[0])
                remaining = float(result[1])
                return RateLimitResult(
                    allowed=allowed,
                    remaining=remaining,
                    retry_after=(amount - remaining) / refill_rate if refill_rate > 0 else 60,
                    limit=max_tokens,
                )
            except Exception as exc:
                logger.warning("redis_ratelimit_failed_fallback_local", error=str(exc))
                self._use_local = True

        # 本地降级
        if key not in self._local_buckets:
            self._local_buckets[key] = LocalTokenBucket(
                max_tokens=max_tokens, refill_rate=refill_rate
            )
        result = self._local_buckets[key].try_consume(amount)
        result.limit = max_tokens
        return result
