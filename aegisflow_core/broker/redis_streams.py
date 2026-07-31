"""Redis Streams backend (consumer groups + a ZSET for delayed redelivery)."""

from __future__ import annotations

import asyncio
import json
import time

from redis.asyncio import Redis

from ..config import settings
from .base import Broker, Message


class RedisStreamBroker(Broker):
    kind = "redis"

    def __init__(self, consumer: str | None = None) -> None:
        self.consumer = consumer or settings.instance_id
        self.group = settings.consumer_group
        self._redis: Redis | None = None
        self._groups: set[str] = set()

    async def start(self) -> None:
        self._redis = Redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=5)
        await self._redis.ping()

    async def stop(self) -> None:
        if self._redis is not None:
            await self._redis.close()
            self._redis = None

    @property
    def redis(self) -> Redis:
        if self._redis is None:
            raise RuntimeError("redis broker not started")
        return self._redis

    async def _ensure_group(self, topic: str) -> None:
        if topic in self._groups:
            return
        try:
            await self.redis.xgroup_create(topic, self.group, id="0", mkstream=True)
        except Exception as exc:  # BUSYGROUP
            if "BUSYGROUP" not in str(exc):
                raise
        self._groups.add(topic)

    async def publish(self, topic: str, key: str, payload: dict, *, delay_s: float = 0.0) -> None:
        body = json.dumps({"key": key, "payload": payload})
        if delay_s > 0:
            await self.redis.zadd(f"{topic}:delayed", {body: time.time() + delay_s})
            return
        await self.redis.xadd(topic, {"body": body}, maxlen=100_000, approximate=True)

    async def _promote_delayed(self, topic: str) -> None:
        due = await self.redis.zrangebyscore(f"{topic}:delayed", 0, time.time(), start=0, num=64)
        for body in due:
            removed = await self.redis.zrem(f"{topic}:delayed", body)
            if removed:
                await self.redis.xadd(topic, {"body": body}, maxlen=100_000, approximate=True)

    async def poll(self, topic: str, *, max_messages: int = 8, wait_s: float = 1.0) -> list[Message]:
        await self._ensure_group(topic)
        await self._promote_delayed(topic)
        try:
            response = await self.redis.xreadgroup(
                self.group, self.consumer, {topic: ">"}, count=max_messages, block=int(wait_s * 1000)
            )
        except Exception:
            await asyncio.sleep(0.5)
            raise
        messages: list[Message] = []
        loop_time = asyncio.get_running_loop().time()
        for _stream, entries in response or []:
            for entry_id, fields in entries:
                envelope = json.loads(fields.get("body", "{}"))
                payload = envelope.get("payload", {})
                messages.append(
                    Message(
                        id=entry_id,
                        topic=topic,
                        key=envelope.get("key", ""),
                        payload=payload,
                        attempt=int(payload.get("attempt", 0)) + 1,
                        handle=entry_id,
                        received_at=loop_time,
                    )
                )
        return messages

    async def ack(self, message: Message) -> None:
        await self.redis.xack(message.topic, self.group, message.handle)

    async def nack(self, message: Message, *, delay_s: float = 0.0) -> None:
        payload = dict(message.payload)
        payload["attempt"] = message.attempt
        await self.publish(message.topic, message.key, payload, delay_s=delay_s)
        await self.redis.xack(message.topic, self.group, message.handle)

    async def claim_stale(self, topic: str, min_idle_ms: int = 45_000) -> int:
        """Reclaim entries whose consumer died (XAUTOCLAIM)."""
        await self._ensure_group(topic)
        _cursor, claimed, _deleted = await self.redis.xautoclaim(
            topic, self.group, self.consumer, min_idle_time=min_idle_ms, count=100
        )
        return len(claimed or [])

    async def depth(self, topic: str) -> int:
        await self._ensure_group(topic)
        try:
            groups = await self.redis.xinfo_groups(topic)
        except Exception:
            return -1
        pending = sum(int(g.get("pending", 0)) for g in groups if g.get("name") == self.group)
        lag = 0
        for g in groups:
            if g.get("name") == self.group and g.get("lag") is not None:
                lag = int(g["lag"] or 0)
        delayed = int(await self.redis.zcard(f"{topic}:delayed") or 0)
        return pending + lag + delayed

    async def healthy(self) -> bool:
        try:
            await self.redis.ping()
            return True
        except Exception:
            return False
