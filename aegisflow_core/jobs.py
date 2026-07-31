"""Job lifecycle: submit, observe, finish, dead-letter, replay.

Everything that touches durable state lives here so the gateway, the worker and
the resilience lab all obey exactly the same invariants.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from .broker import get_broker
from .cache import cache
from .chaos import chaos
from .config import settings
from .db import as_utc, iso, session_scope
from .inference import registry
from .models import (
    BrokerMessage,
    ChaosFault,
    DeadLetter,
    Job,
    JobEvent,
    OutboxEvent,
    ProcessedMessage,
    WorkerHeartbeat,
    new_id,
    utcnow,
)
from .observability import (
    broker_publish,
    job_compute,
    job_e2e,
    job_retries,
    jobs_completed,
    jobs_submitted,
)
from .observability import (
    dead_letters as dlq_metric,
)
from .observability import (
    outbox_pending as outbox_gauge,
)
from .observability import (
    queue_depth as queue_gauge,
)
from .resilience import CircuitBreaker, PermanentError, with_timeout

log = logging.getLogger("aegisflow.jobs")

TERMINAL = ("succeeded", "failed", "dlq")
publish_breaker = CircuitBreaker("broker.publish", failure_threshold=3, reset_timeout_s=5.0)

# Fire-and-forget publishes need a strong reference, otherwise the event loop is
# free to garbage-collect the task before it runs.
_background: set[asyncio.Task] = set()


def spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)
    return task


# --------------------------------------------------------------------------
# serialisation
# --------------------------------------------------------------------------
def job_view(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "status": job.status,
        "model": job.model,
        "model_version": job.model_version,
        "input": job.payload or {},
        "result": job.result,
        "error": job.error,
        "attempts": job.attempts,
        "degraded": bool(job.degraded),
        "priority": job.priority,
        "queue_ms": round(job.queue_ms, 2) if job.queue_ms is not None else None,
        "compute_ms": round(job.compute_ms, 2) if job.compute_ms is not None else None,
        "total_ms": round(job.total_ms, 2) if job.total_ms is not None else None,
        "worker_id": job.worker_id,
        "trace_id": job.trace_id,
        "tenant": job.tenant,
        "created_at": iso(job.created_at),
        "finished_at": iso(job.finished_at),
    }


# --------------------------------------------------------------------------
# submit path
# --------------------------------------------------------------------------
async def submit_job(
    *,
    model: str,
    payload: dict[str, Any],
    priority: int = 5,
    idempotency_key: str | None = None,
    tenant: str = "public",
    source: str = "api",
    trace_id: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Persist the job and its publish intent in one transaction."""
    # Fail fast at the edge: an unknown model or an empty payload never becomes
    # a queued job that a worker has to reject later.
    registry.validate(model, payload)

    if idempotency_key:
        existing = await _by_idempotency(idempotency_key)
        if existing:
            return job_view(existing), True

    job_id = new_id("job")
    job = Job(
        id=job_id,
        idempotency_key=idempotency_key,
        model=model,
        payload=payload,
        status="queued",
        priority=priority,
        tenant=tenant,
        source=source,
        trace_id=trace_id or job_id,
    )
    # The envelope carries the input so a worker needs no extra read before it
    # can compute - one transaction per job on the hot path.
    envelope = {
        "job_id": job_id,
        "model": model,
        "input": payload,
        "priority": priority,
        "attempt": 0,
        "trace_id": job.trace_id,
    }
    inline_publish = get_broker().kind == "db"
    if inline_publish and await chaos.is_disrupted("broker"):
        # Broker treated as unreachable: skip the enqueue, keep the job durable
        # and let the relay publish it once the outage is over. This is the whole
        # point of the outbox, so the drill has to exercise it.
        inline_publish = False

    try:
        async with session_scope() as session:
            session.add(job)
            session.add(JobEvent(job_id=job_id, type="submitted", data={"model": model, "source": source}))
            outbox = OutboxEvent(
                topic=settings.topic_requests,
                message_key=job_id,
                payload=envelope,
                # With the DB broker the enqueue happens in this very transaction,
                # so the outbox row is born already published.
                published_at=utcnow() if inline_publish else None,
            )
            session.add(outbox)
            if inline_publish:
                session.add(
                    BrokerMessage(topic=settings.topic_requests, message_key=job_id, payload=envelope, attempt=0)
                )
            await session.flush()
            outbox_id = outbox.id
    except IntegrityError:
        if idempotency_key:
            existing = await _by_idempotency(idempotency_key)
            if existing:
                return job_view(existing), True
        raise

    jobs_submitted.labels(model=model, source=source).inc()
    if inline_publish:
        broker_publish.labels(topic=settings.topic_requests, result="inline").inc()
    else:
        # Network brokers: publish outside the transaction for low latency and
        # let the relay pick the row up if the broker is unreachable.
        spawn(_publish_now(outbox_id, settings.topic_requests, job_id, envelope))
    return job_view(job), False


async def _by_idempotency(key: str) -> Job | None:
    async with session_scope() as session:
        return (await session.execute(select(Job).where(Job.idempotency_key == key))).scalar_one_or_none()


async def _publish_now(outbox_id: int, topic: str, key: str, envelope: dict) -> None:
    """Low-latency publish. The relay is the durable safety net if this fails."""
    try:
        broker = get_broker()

        async def _do() -> None:
            await with_timeout(broker.publish(topic, key, envelope), 1500, "broker.publish")

        await publish_breaker.call(_do)
        async with session_scope() as session:
            await session.execute(
                update(OutboxEvent)
                .where(OutboxEvent.id == outbox_id, OutboxEvent.published_at.is_(None))
                .values(published_at=utcnow())
            )
        broker_publish.labels(topic=topic, result="ok").inc()
    except Exception as exc:
        broker_publish.labels(topic=topic, result="deferred").inc()
        log.debug("fast publish deferred to relay: %s", exc)


async def wait_for_result(job_id: str, wait_ms: int, poll_ms: int = 60) -> dict[str, Any] | None:
    """Optional synchronous facade over the async pipeline."""
    deadline = asyncio.get_running_loop().time() + wait_ms / 1000
    while True:
        view = await get_job(job_id)
        if view and view["status"] in TERMINAL:
            return view
        if asyncio.get_running_loop().time() >= deadline:
            return view
        await asyncio.sleep(poll_ms / 1000)


# --------------------------------------------------------------------------
# read path
# --------------------------------------------------------------------------
async def get_job(job_id: str) -> dict[str, Any] | None:
    cached = await cache.get(f"job:{job_id}")
    if cached and cached.get("status") in TERMINAL:
        return cached
    async with session_scope() as session:
        job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
    if job is None:
        return None
    view = job_view(job)
    if view["status"] in TERMINAL:
        await cache.set(f"job:{job_id}", view)
    return view


async def list_jobs(
    *, status: str | None = None, model: str | None = None, tenant: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    stmt = select(Job).order_by(Job.created_at.desc()).limit(min(500, max(1, limit)))
    if status:
        stmt = stmt.where(Job.status == status)
    if model:
        stmt = stmt.where(Job.model == model)
    if tenant:
        stmt = stmt.where(Job.tenant == tenant)
    async with session_scope() as session:
        rows = (await session.execute(stmt)).scalars().all()
    return [job_view(row) for row in rows]


async def tail_events(after_id: int = 0, limit: int = 100) -> list[dict[str, Any]]:
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(JobEvent).where(JobEvent.id > after_id).order_by(JobEvent.id).limit(min(500, limit))
            )
        ).scalars().all()
    return [
        {"id": row.id, "job_id": row.job_id, "type": row.type, "data": row.data or {}, "at": iso(row.at)}
        for row in rows
    ]


async def latest_event_id() -> int:
    async with session_scope() as session:
        return int(await session.scalar(select(func.coalesce(func.max(JobEvent.id), 0))) or 0)


# --------------------------------------------------------------------------
# worker path
# --------------------------------------------------------------------------
async def already_processed(dedupe_key: str) -> bool:
    async with session_scope() as session:
        found = await session.scalar(
            select(ProcessedMessage.message_key).where(ProcessedMessage.message_key == dedupe_key)
        )
    return found is not None


async def mark_running(job_id: str, worker_id: str, attempt: int) -> dict[str, Any] | None:
    now = utcnow()
    async with session_scope() as session:
        job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
        if job is None:
            return None
        if job.status in TERMINAL:
            return job_view(job)
        created = as_utc(job.created_at) or now
        job.status = "running"
        job.attempts = attempt
        job.worker_id = worker_id
        job.started_at = now
        job.updated_at = now
        job.queue_ms = (now - created).total_seconds() * 1000
        session.add(JobEvent(job_id=job_id, type="running", data={"worker": worker_id, "attempt": attempt}))
        return job_view(job)


async def finish_job(
    *,
    job_id: str,
    status: str,
    dedupe_key: str | None,
    result: dict | None = None,
    error: str | None = None,
    model_version: str | None = None,
    degraded: bool = False,
    compute_ms: float | None = None,
    worker_id: str | None = None,
    attempt: int = 1,
    dlq_payload: dict | None = None,
    ack_message_id: int | None = None,
) -> dict[str, Any] | None:
    """Terminal state, audit event, dedupe marker (and, for the DB broker, the
    queue ack) committed in a single transaction.

    With the DB broker that makes processing exactly-once, because the ack can
    never be lost after the result was written. Kafka/Redis commit offsets
    separately, which is why the dedupe table exists.
    """
    now = utcnow()
    async with session_scope() as session:
        job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
        if job is None:
            return None
        if job.status in TERMINAL:
            # Duplicate delivery of an already finished job: ack, change nothing.
            if ack_message_id is not None:
                await session.execute(
                    update(BrokerMessage).where(BrokerMessage.id == ack_message_id).values(status="done")
                )
            return job_view(job)
        created = as_utc(job.created_at) or now
        job.status = status
        job.result = result
        job.error = error
        job.degraded = degraded
        job.attempts = attempt
        job.model_version = model_version or job.model_version
        job.compute_ms = compute_ms
        job.worker_id = worker_id or job.worker_id
        job.finished_at = now
        job.updated_at = now
        job.total_ms = (now - created).total_seconds() * 1000
        if job.queue_ms is None:
            # No separate "running" write happened: derive the queue wait.
            job.queue_ms = max(0.0, job.total_ms - (compute_ms or 0.0))
        session.add(
            JobEvent(
                job_id=job_id,
                type=status,
                data={
                    "model": job.model,
                    "attempt": attempt,
                    "compute_ms": round(compute_ms or 0, 2),
                    "total_ms": round(job.total_ms, 2),
                    "degraded": degraded,
                    "error": error,
                    "label": (result or {}).get("label"),
                },
            )
        )
        if dedupe_key:
            session.add(ProcessedMessage(message_key=dedupe_key, job_id=job_id))
        if ack_message_id is not None:
            await session.execute(
                update(BrokerMessage)
                .where(BrokerMessage.id == ack_message_id)
                .values(status="done", lease_until=None)
            )
        if status == "dlq":
            session.add(
                DeadLetter(
                    job_id=job_id,
                    topic=settings.topic_dlq,
                    payload=dlq_payload or {"job_id": job_id, "model": job.model, "input": job.payload},
                    error=error or "unknown",
                    attempts=attempt,
                )
            )
        view = job_view(job)

    jobs_completed.labels(model=view["model"], status=status).inc()
    if compute_ms is not None:
        job_compute.labels(model=view["model"]).observe(compute_ms / 1000)
    if view["total_ms"] is not None:
        job_e2e.labels(model=view["model"]).observe(view["total_ms"] / 1000)
    if status == "dlq":
        dlq_metric.labels(model=view["model"]).inc()
    await cache.set(f"job:{job_id}", view)
    return view


async def record_retry(job_id: str, attempt: int, reason: str, model: str, delay_s: float) -> None:
    job_retries.labels(model=model, reason=reason[:32]).inc()
    async with session_scope() as session:
        await session.execute(
            update(Job).where(Job.id == job_id).values(status="queued", attempts=attempt, updated_at=utcnow())
        )
        session.add(
            JobEvent(
                job_id=job_id,
                type="retry",
                data={"attempt": attempt, "reason": reason, "retry_in_s": round(delay_s, 3)},
            )
        )


async def heartbeat(
    *, worker_id: str, hostname: str, models: dict, inflight: int, processed: int, failed: int, state: str
) -> None:
    async with session_scope() as session:
        row = (
            await session.execute(select(WorkerHeartbeat).where(WorkerHeartbeat.worker_id == worker_id))
        ).scalar_one_or_none()
        if row is None:
            row = WorkerHeartbeat(worker_id=worker_id)
            session.add(row)
        row.hostname = hostname
        row.version = settings.version
        row.models = models
        row.inflight = inflight
        row.processed = processed
        row.failed = failed
        row.state = state
        row.last_seen = utcnow()


# --------------------------------------------------------------------------
# dead letters
# --------------------------------------------------------------------------
async def list_dead_letters(limit: int = 50, include_replayed: bool = False) -> list[dict[str, Any]]:
    stmt = select(DeadLetter).order_by(DeadLetter.created_at.desc()).limit(limit)
    if not include_replayed:
        stmt = stmt.where(DeadLetter.replayed_at.is_(None))
    async with session_scope() as session:
        rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": row.id,
            "job_id": row.job_id,
            "error": row.error,
            "attempts": row.attempts,
            "payload": row.payload,
            "created_at": iso(row.created_at),
            "replayed_at": iso(row.replayed_at),
        }
        for row in rows
    ]


async def replay_dead_letter(dlq_id: int) -> dict[str, Any]:
    async with session_scope() as session:
        row = (await session.execute(select(DeadLetter).where(DeadLetter.id == dlq_id))).scalar_one_or_none()
        if row is None:
            raise PermanentError(f"dead letter {dlq_id} not found")
        if row.replayed_at is not None:
            raise PermanentError(f"dead letter {dlq_id} was already replayed")
        job = (await session.execute(select(Job).where(Job.id == row.job_id))).scalar_one_or_none()
        if job is None:
            raise PermanentError(f"job {row.job_id} no longer exists")
        envelope = {
            "job_id": job.id,
            "model": job.model,
            "input": job.payload or {},
            "priority": job.priority,
            "attempt": 0,
            "trace_id": job.trace_id,
            "replay_of": dlq_id,
        }
        job.status = "queued"
        job.error = None
        job.attempts = 0
        job.finished_at = None
        job.updated_at = utcnow()
        row.replayed_at = utcnow()
        session.add(OutboxEvent(topic=settings.topic_requests, message_key=job.id, payload=envelope))
        session.add(JobEvent(job_id=job.id, type="replayed", data={"dlq_id": dlq_id}))
        view = job_view(job)
    await cache.delete(f"job:{job.id}")
    return view


async def replay_all_dead_letters(limit: int = 100) -> list[str]:
    replayed = []
    for row in await list_dead_letters(limit=limit):
        try:
            view = await replay_dead_letter(row["id"])
            replayed.append(view["id"])
        except PermanentError:
            continue
    return replayed


# --------------------------------------------------------------------------
# platform statistics (drives the dashboard and the CV numbers)
# --------------------------------------------------------------------------
def _percentile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return round(ordered[index], 2)


async def stats(window_minutes: int = 15) -> dict[str, Any]:
    since = utcnow() - timedelta(minutes=window_minutes)
    minute_ago = utcnow() - timedelta(seconds=60)
    broker = get_broker()

    async with session_scope() as session:
        status_rows = (await session.execute(select(Job.status, func.count()).group_by(Job.status))).all()
        window_rows = (
            await session.execute(
                select(Job.status, func.count()).where(Job.created_at >= since).group_by(Job.status)
            )
        ).all()
        finished_last_min = int(
            await session.scalar(
                select(func.count()).select_from(Job).where(Job.finished_at >= minute_ago, Job.status.in_(TERMINAL))
            )
            or 0
        )
        latency_rows = (
            await session.execute(
                select(Job.total_ms, Job.compute_ms, Job.queue_ms)
                .where(Job.status == "succeeded", Job.total_ms.is_not(None))
                .order_by(Job.finished_at.desc())
                .limit(300)
            )
        ).all()
        oldest_queued = await session.scalar(
            select(func.min(Job.created_at)).where(Job.status.in_(("queued", "running")))
        )
        pending_outbox = int(
            await session.scalar(select(func.count()).select_from(OutboxEvent).where(OutboxEvent.published_at.is_(None)))
            or 0
        )
        dlq_open = int(
            await session.scalar(select(func.count()).select_from(DeadLetter).where(DeadLetter.replayed_at.is_(None)))
            or 0
        )
        retries = int(
            await session.scalar(
                select(func.count()).select_from(JobEvent).where(JobEvent.type == "retry", JobEvent.at >= since)
            )
            or 0
        )
        workers = (
            await session.execute(
                select(WorkerHeartbeat).where(WorkerHeartbeat.last_seen >= utcnow() - timedelta(seconds=30))
            )
        ).scalars().all()
        active_faults = int(
            await session.scalar(
                select(func.count())
                .select_from(ChaosFault)
                .where(ChaosFault.cleared_at.is_(None), ChaosFault.expires_at > utcnow())
            )
            or 0
        )
        inflight_messages = int(
            await session.scalar(
                select(func.count()).select_from(BrokerMessage).where(BrokerMessage.status == "inflight")
            )
            or 0
        )

    by_status = {status: int(count) for status, count in status_rows}
    window = {status: int(count) for status, count in window_rows}
    total = sum(by_status.values())
    terminal = sum(by_status.get(s, 0) for s in TERMINAL)
    in_flight = by_status.get("queued", 0) + by_status.get("running", 0)
    succeeded_window = window.get("succeeded", 0)
    terminal_window = sum(window.get(s, 0) for s in TERMINAL)

    totals = [float(row[0]) for row in latency_rows if row[0] is not None]
    computes = [float(row[1]) for row in latency_rows if row[1] is not None]
    queues = [float(row[2]) for row in latency_rows if row[2] is not None]

    try:
        depth = await broker.depth(settings.topic_requests)
    except Exception:
        depth = -1
    if depth >= 0:
        queue_gauge.set(depth)
    outbox_gauge.set(pending_outbox)

    oldest_age = None
    oldest_aware = as_utc(oldest_queued) if isinstance(oldest_queued, datetime) else None
    if oldest_aware:
        oldest_age = round((utcnow() - oldest_aware).total_seconds(), 2)

    return {
        "generated_at": iso(utcnow()),
        "platform": {
            "env": settings.app_env,
            "version": settings.version,
            "broker": broker.kind,
            "database": "postgres" if not settings.is_sqlite else "sqlite",
            "cache": cache.status(),
            "topics": {"requests": settings.topic_requests, "dlq": settings.topic_dlq},
        },
        "jobs": {
            "total": total,
            "by_status": by_status,
            "window_minutes": window_minutes,
            "window_by_status": window,
            "in_flight": in_flight,
        },
        "throughput": {
            "completed_last_60s": finished_last_min,
            "per_second": round(finished_last_min / 60, 2),
            "window_completed": terminal_window,
        },
        "latency_ms": {
            "p50": _percentile(totals, 0.50),
            "p95": _percentile(totals, 0.95),
            "p99": _percentile(totals, 0.99),
            "avg": round(sum(totals) / len(totals), 2) if totals else None,
            "compute_p95": _percentile(computes, 0.95),
            "queue_p95": _percentile(queues, 0.95),
            "samples": len(totals),
        },
        "queue": {
            "depth": depth,
            "inflight_messages": inflight_messages,
            "outbox_pending": pending_outbox,
            "oldest_pending_age_s": oldest_age,
        },
        "reliability": {
            "success_rate_window": round(succeeded_window / terminal_window, 4) if terminal_window else None,
            "retries_window": retries,
            "dlq_open": dlq_open,
            "unaccounted_jobs": total - terminal - in_flight,
            "breaker": publish_breaker.snapshot(),
        },
        "workers": [
            {
                "worker_id": w.worker_id,
                "hostname": w.hostname,
                "state": w.state,
                "inflight": w.inflight,
                "processed": w.processed,
                "failed": w.failed,
                "models": w.models,
                "last_seen": iso(w.last_seen),
            }
            for w in workers
        ],
        "chaos": {"active": active_faults},
        "models": registry.catalog(),
    }


async def prune_old_data(retention_hours: int | None = None) -> dict[str, int]:
    hours = retention_hours or settings.retention_hours
    cutoff = utcnow() - timedelta(hours=hours)
    async with session_scope() as session:
        events = await session.execute(delete(JobEvent).where(JobEvent.at < cutoff))
        processed = await session.execute(delete(ProcessedMessage).where(ProcessedMessage.processed_at < cutoff))
        published = await session.execute(
            delete(OutboxEvent).where(OutboxEvent.published_at.is_not(None), OutboxEvent.created_at < cutoff)
        )
        stale_workers = await session.execute(
            delete(WorkerHeartbeat).where(WorkerHeartbeat.last_seen < utcnow() - timedelta(hours=1))
        )
    return {
        "job_events": int(events.rowcount or 0),
        "processed_messages": int(processed.rowcount or 0),
        "outbox": int(published.rowcount or 0),
        "workers": int(stale_workers.rowcount or 0),
    }
