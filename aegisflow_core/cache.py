"""Cache-aside helper that degrades to an in-process TTL map when Redis is down."""

from __future__ import annotations

import json
import time
from typing import Any

from .config import settings
from .observability import cache_events

try:  # redis is optional at runtime
    from redis.asyncio import Redis
except Exception:  # pragma: no cover
    Redis = None  # type: ignore[assignment]

_LOCAL_MAX = 5000


class Cache:
    def __init__(self) -> None:
        self._redis: Any | None = None
        self._local: dict[str, tuple[float, Any]] = {}
        self._degraded_until = 0.0
        self.backend = "memory"

    async def connect(self) -> None:
        if Redis is None or not (settings.redis_enabled or settings.broker == "redis"):
            return  # laptop mode: in-process cache only
        try:
            client = Redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=1.5)
            await client.ping()
            self._redis = client
            self.backend = "redis"
        except Exception:
            self._redis = None

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.close()
            self._redis = None

    def _healthy(self) -> bool:
        return self._redis is not None and time.monotonic() >= self._degraded_until

    def _degrade(self) -> None:
        self._degraded_until = time.monotonic() + 10.0
        self.backend = "memory (redis degraded)"

    async def get(self, key: str) -> Any | None:
        if self._healthy():
            try:
                raw = await self._redis.get(key)  # type: ignore[union-attr]
                cache_events.labels(event="hit" if raw else "miss").inc()
                return json.loads(raw) if raw else None
            except Exception:
                self._degrade()
        entry = self._local.get(key)
        if entry and entry[0] > time.time():
            cache_events.labels(event="hit").inc()
            return entry[1]
        self._local.pop(key, None)
        cache_events.labels(event="miss").inc()
        return None

    async def set(self, key: str, value: Any, ttl_s: int | None = None) -> None:
        ttl = ttl_s or settings.cache_ttl_s
        if self._healthy():
            try:
                await self._redis.set(key, json.dumps(value, default=str), ex=ttl)  # type: ignore[union-attr]
                return
            except Exception:
                self._degrade()
        if len(self._local) > _LOCAL_MAX:
            self._local.clear()
        self._local[key] = (time.time() + ttl, value)

    async def delete(self, key: str) -> None:
        if self._healthy():
            try:
                await self._redis.delete(key)  # type: ignore[union-attr]
            except Exception:
                self._degrade()
        self._local.pop(key, None)

    def status(self) -> dict:
        return {"backend": self.backend, "local_keys": len(self._local)}


cache = Cache()
