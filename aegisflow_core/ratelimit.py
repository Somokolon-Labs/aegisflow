"""Token-bucket rate limiting.

Uses a Redis Lua script when Redis is available (correct across replicas) and
falls back to a per-process bucket otherwise - a limiter that fails open is
better than an edge that fails closed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .config import settings
from .observability import rate_limited

try:
    from redis.asyncio import Redis
except Exception:  # pragma: no cover
    Redis = None  # type: ignore[assignment]

_LUA = """
local key = KEYS[1]
local rate = tonumber(ARGV[1])
local burst = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])
local bucket = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(bucket[1])
local ts = tonumber(bucket[2])
if tokens == nil then tokens = burst; ts = now end
tokens = math.min(burst, tokens + (now - ts) * rate)
local allowed = 0
if tokens >= cost then tokens = tokens - cost; allowed = 1 end
redis.call('HMSET', key, 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', key, 120)
return {allowed, tostring(tokens)}
"""


@dataclass
class Decision:
    allowed: bool
    remaining: float
    limit: int
    backend: str

    def headers(self) -> dict[str, str]:
        return {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(max(0, int(self.remaining))),
            "X-RateLimit-Policy": "token-bucket",
        }


class RateLimiter:
    def __init__(self) -> None:
        self._redis = None
        self._script = None
        self._local: dict[str, tuple[float, float]] = {}
        self.backend = "in-process"

    async def connect(self) -> None:
        if Redis is None or not (settings.redis_enabled or settings.broker == "redis"):
            return
        try:
            client = Redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=1.5)
            await client.ping()
            self._redis = client
            self._script = client.register_script(_LUA)
            self.backend = "redis"
        except Exception:
            self._redis = None

    async def check(self, identity: str, cost: int = 1) -> Decision:
        rate = settings.rate_limit_per_minute / 60.0
        burst = float(settings.rate_limit_burst)
        now = time.time()
        if self._script is not None:
            try:
                allowed, tokens = await self._script(keys=[f"rl:{identity}"], args=[rate, burst, now, cost])
                decision = Decision(bool(int(allowed)), float(tokens), settings.rate_limit_per_minute, "redis")
                if not decision.allowed:
                    rate_limited.inc()
                return decision
            except Exception:
                self._redis = None
                self._script = None
                self.backend = "in-process (redis degraded)"
        tokens, ts = self._local.get(identity, (burst, now))
        tokens = min(burst, tokens + (now - ts) * rate)
        allowed_local = tokens >= cost
        if allowed_local:
            tokens -= cost
        self._local[identity] = (tokens, now)
        if not allowed_local:
            rate_limited.inc()
        return Decision(allowed_local, tokens, settings.rate_limit_per_minute, self.backend)


limiter = RateLimiter()
