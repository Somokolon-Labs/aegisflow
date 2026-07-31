"""Durable queue backed by the relational database.

Claiming is a single atomic UPDATE stamped with a unique claim token, which is
safe on both Postgres and SQLite and needs no advisory locks. Un-acked messages
whose lease expired are returned to the queue by the reaper in the relay
service - that is how a hard-killed worker never loses a job.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

from sqlalchemy import func, select, update

from ..config import settings
from ..db import session_scope
from ..models import BrokerMessage, utcnow
from .base import Broker, Message


class DbBroker(Broker):
    kind = "db"

    def __init__(self, consumer: str | None = None) -> None:
        self.consumer = consumer or settings.instance_id

    async def start(self) -> None:  # nothing to connect to
        return None

    async def stop(self) -> None:
        return None

    async def publish(self, topic: str, key: str, payload: dict, *, delay_s: float = 0.0) -> None:
        async with session_scope() as session:
            session.add(
                BrokerMessage(
                    topic=topic,
                    message_key=key,
                    payload=payload,
                    available_at=utcnow() + timedelta(seconds=delay_s),
                    attempt=int(payload.get("attempt", 0)),
                )
            )

    async def poll(self, topic: str, *, max_messages: int = 8, wait_s: float = 1.0) -> list[Message]:
        token = f"{self.consumer}:{uuid.uuid4().hex[:12]}"
        deadline = asyncio.get_running_loop().time() + wait_s
        while True:
            claimed = await self._claim(topic, max_messages, token)
            if claimed or asyncio.get_running_loop().time() >= deadline:
                return claimed
            await asyncio.sleep(min(0.15, max(0.02, wait_s / 8)))

    async def _claim(self, topic: str, limit: int, token: str) -> list[Message]:
        now = utcnow()
        lease_until = now + timedelta(seconds=settings.visibility_timeout_s)
        candidates = (
            select(BrokerMessage.id)
            .where(
                BrokerMessage.topic == topic,
                BrokerMessage.status == "pending",
                BrokerMessage.available_at <= now,
            )
            .order_by(BrokerMessage.available_at, BrokerMessage.id)
            .limit(limit)
        )
        if not settings.is_sqlite:
            candidates = candidates.with_for_update(skip_locked=True)

        async with session_scope() as session:
            ids = list((await session.execute(candidates)).scalars().all())
            if not ids:
                return []
            await session.execute(
                update(BrokerMessage)
                .where(BrokerMessage.id.in_(ids), BrokerMessage.status == "pending")
                .values(
                    status="inflight",
                    consumer=token,
                    lease_until=lease_until,
                    attempt=BrokerMessage.attempt + 1,
                )
            )
            rows = (
                await session.execute(
                    select(BrokerMessage).where(BrokerMessage.consumer == token, BrokerMessage.status == "inflight")
                )
            ).scalars().all()

        loop_time = asyncio.get_running_loop().time()
        return [
            Message(
                id=str(row.id),
                topic=row.topic,
                key=row.message_key,
                payload=row.payload or {},
                attempt=row.attempt,
                handle=row.id,
                received_at=loop_time,
            )
            for row in rows
        ]

    async def ack(self, message: Message) -> None:
        async with session_scope() as session:
            await session.execute(
                update(BrokerMessage).where(BrokerMessage.id == int(message.handle)).values(status="done", lease_until=None)
            )

    async def nack(self, message: Message, *, delay_s: float = 0.0) -> None:
        async with session_scope() as session:
            await session.execute(
                update(BrokerMessage)
                .where(BrokerMessage.id == int(message.handle))
                .values(
                    status="pending",
                    consumer=None,
                    lease_until=None,
                    available_at=utcnow() + timedelta(seconds=delay_s),
                )
            )

    async def depth(self, topic: str) -> int:
        async with session_scope() as session:
            total = await session.scalar(
                select(func.count())
                .select_from(BrokerMessage)
                .where(BrokerMessage.topic == topic, BrokerMessage.status.in_(("pending", "inflight")))
            )
        return int(total or 0)

    async def reap_expired_leases(self) -> int:
        """Return leased-but-never-acked messages to the queue."""
        async with session_scope() as session:
            result = await session.execute(
                update(BrokerMessage)
                .where(BrokerMessage.status == "inflight", BrokerMessage.lease_until < utcnow())
                .values(status="pending", consumer=None, lease_until=None, available_at=utcnow())
            )
        return int(result.rowcount or 0)

    async def prune(self, older_than_hours: int) -> int:
        from sqlalchemy import delete

        cutoff = utcnow() - timedelta(hours=older_than_hours)
        async with session_scope() as session:
            result = await session.execute(
                delete(BrokerMessage).where(BrokerMessage.status == "done", BrokerMessage.created_at < cutoff)
            )
        return int(result.rowcount or 0)
