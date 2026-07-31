"""AegisFlow relay - outbox publisher, lease reaper and janitor.

This service is what makes "no lost work" a property of the system rather than a
hope:

1. **Outbox drain** - every accepted job has a row in ``outbox``. The relay
   publishes it to the broker and only then stamps ``published_at``. If the
   broker is down the rows simply accumulate and drain when it returns.
2. **Lease reaper** - messages leased by a worker that died are returned to the
   queue once the visibility timeout expires.
3. **Janitor** - prunes acked messages, expired dedupe keys and old audit rows,
   and keeps the queue-depth gauges warm for Prometheus.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
import time
from datetime import timedelta
from typing import Any

import uvicorn
from fastapi import FastAPI, Response
from sqlalchemy import func, select, update

from aegisflow_core import jobs as jobsvc
from aegisflow_core.broker import get_broker
from aegisflow_core.broker.dbq import DbBroker
from aegisflow_core.chaos import chaos
from aegisflow_core.config import settings
from aegisflow_core.db import dispose, init_db, session_scope
from aegisflow_core.models import OutboxEvent, utcnow
from aegisflow_core.observability import (
    broker_publish,
    log_event,
    outbox_pending,
    queue_depth,
    render_metrics,
    service_up,
    setup_logging,
)
from aegisflow_core.resilience import CircuitBreaker, backoff_delay, with_timeout

settings.service_name = "relay"
log = setup_logging()

BATCH = 100
IDLE_SLEEP_S = 0.15
breaker = CircuitBreaker("relay.publish", failure_threshold=5, reset_timeout_s=5.0)


class Relay:
    def __init__(self) -> None:
        self.broker = get_broker(f"relay-{settings.instance_id}")
        self.stopping = asyncio.Event()
        self.published = 0
        self.deferred = 0
        self.reaped = 0
        self.pruned = 0
        self.started_at = time.time()
        self.state = "starting"

    async def start(self) -> None:
        await init_db()
        await self.broker.start()
        service_up.labels(service="relay", instance=settings.instance_id).set(1)
        self.state = "healthy"
        log_event(log, "info", "relay ready", broker=self.broker.kind)

    async def stop(self) -> None:
        self.stopping.set()
        await self.broker.stop()
        await dispose()
        service_up.labels(service="relay", instance=settings.instance_id).set(0)
        log_event(log, "info", "relay stopped", published=self.published, reaped=self.reaped)

    # ---- 1. outbox --------------------------------------------------------
    async def drain_outbox(self) -> None:
        while not self.stopping.is_set():
            try:
                rows = await self._claim_outbox()
                if not rows:
                    await asyncio.sleep(IDLE_SLEEP_S)
                    continue
                for row in rows:
                    await self._publish(row)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.state = "outbox error"
                log_event(log, "error", "outbox drain failed", error=str(exc))
                await asyncio.sleep(1.0)

    async def _claim_outbox(self) -> list[dict[str, Any]]:
        now = utcnow()
        stmt = (
            select(OutboxEvent)
            .where(OutboxEvent.published_at.is_(None), OutboxEvent.available_at <= now)
            .order_by(OutboxEvent.id)
            .limit(BATCH)
        )
        async with session_scope() as session:
            rows = (await session.execute(stmt)).scalars().all()
            return [
                {
                    "id": row.id,
                    "topic": row.topic,
                    "key": row.message_key,
                    "payload": row.payload or {},
                    "attempts": row.attempts,
                }
                for row in rows
            ]

    async def _publish(self, row: dict[str, Any]) -> None:
        try:
            await chaos.apply("broker")

            async def _do() -> None:
                await with_timeout(
                    self.broker.publish(row["topic"], row["key"], row["payload"]), 3000, "relay.publish"
                )

            await breaker.call(_do)
        except Exception as exc:
            delay = backoff_delay(row["attempts"] + 1, base_ms=200, max_ms=5000)
            async with session_scope() as session:
                await session.execute(
                    update(OutboxEvent)
                    .where(OutboxEvent.id == row["id"])
                    .values(
                        attempts=OutboxEvent.attempts + 1,
                        last_error=str(exc)[:500],
                        available_at=utcnow() + timedelta(seconds=delay),
                    )
                )
            self.deferred += 1
            broker_publish.labels(topic=row["topic"], result="error").inc()
            self.state = "broker degraded"
            return

        async with session_scope() as session:
            await session.execute(
                update(OutboxEvent)
                .where(OutboxEvent.id == row["id"], OutboxEvent.published_at.is_(None))
                .values(published_at=utcnow())
            )
        self.published += 1
        self.state = "healthy"
        broker_publish.labels(topic=row["topic"], result="ok").inc()

    # ---- 2. leases -------------------------------------------------------
    async def reap_loop(self) -> None:
        while not self.stopping.is_set():
            try:
                if isinstance(self.broker, DbBroker):
                    requeued = await self.broker.reap_expired_leases()
                else:
                    claim = getattr(self.broker, "claim_stale", None)
                    requeued = await claim(settings.topic_requests) if claim else 0
                if requeued:
                    self.reaped += requeued
                    log_event(log, "warning", "requeued messages from expired leases", count=requeued)
            except Exception as exc:
                log_event(log, "error", "reaper failed", error=str(exc))
            await asyncio.sleep(max(2.0, settings.visibility_timeout_s / 6))

    # ---- 3. janitor ------------------------------------------------------
    async def janitor_loop(self) -> None:
        tick = 0
        while not self.stopping.is_set():
            try:
                depth = await self.broker.depth(settings.topic_requests)
                if depth >= 0:
                    queue_depth.set(depth)
                async with session_scope() as session:
                    pending = int(
                        await session.scalar(
                            select(func.count()).select_from(OutboxEvent).where(OutboxEvent.published_at.is_(None))
                        )
                        or 0
                    )
                outbox_pending.set(pending)
                await chaos.list_active(force=True)

                tick += 1
                if tick % 20 == 0:  # roughly every minute
                    stats = await jobsvc.prune_old_data()
                    if isinstance(self.broker, DbBroker):
                        stats["broker_messages"] = await self.broker.prune(settings.retention_hours)
                    removed = sum(stats.values())
                    if removed:
                        self.pruned += removed
                        log_event(log, "info", "janitor pruned rows", **stats)
            except Exception as exc:
                log_event(log, "error", "janitor failed", error=str(exc))
            await asyncio.sleep(3.0)

    def snapshot(self) -> dict[str, Any]:
        return {
            "service": "relay",
            "state": self.state,
            "published": self.published,
            "deferred": self.deferred,
            "requeued": self.reaped,
            "pruned": self.pruned,
            "breaker": breaker.snapshot(),
            "broker": self.broker.kind,
            "uptime_s": round(time.time() - self.started_at, 1),
        }


relay = Relay()
admin = FastAPI(title="AegisFlow Relay", version=settings.version, docs_url=None, openapi_url=None)


@admin.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", **relay.snapshot()}


@admin.get("/health/ready")
async def ready(response: Response) -> dict[str, Any]:
    response.status_code = 200 if relay.state != "starting" else 503
    return relay.snapshot()


@admin.get("/metrics")
async def metrics() -> Response:
    payload, content_type = render_metrics()
    return Response(content=payload, media_type=content_type)


async def main() -> None:
    await relay.start()
    config = uvicorn.Config(admin, host="0.0.0.0", port=settings.metrics_port, log_config=None, access_log=False)
    server = uvicorn.Server(config)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _request_stop(*_args) -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, AttributeError):
            loop.add_signal_handler(sig, _request_stop)
    if sys.platform == "win32":
        signal.signal(signal.SIGINT, _request_stop)

    tasks = [
        asyncio.create_task(relay.drain_outbox(), name="outbox"),
        asyncio.create_task(relay.reap_loop(), name="reaper"),
        asyncio.create_task(relay.janitor_loop(), name="janitor"),
        asyncio.create_task(server.serve(), name="admin-api"),
    ]
    await stop_event.wait()
    server.should_exit = True
    await relay.stop()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
