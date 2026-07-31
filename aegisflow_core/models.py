"""Durable state for the platform.

Design notes
------------
* ``jobs`` is the single source of truth for a request. Nothing is acknowledged
  to a client before the row is committed.
* ``outbox`` implements the transactional-outbox pattern: a job and its
  "publish to broker" intent are written in the same transaction, so a broker
  outage can never lose work.
* ``processed_messages`` is the inbox/dedupe table that turns at-least-once
  delivery into effectively-once processing.
* ``broker_messages`` is the storage for the built-in DB broker (used when no
  Kafka/Redis is available) with lease based visibility timeouts.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# SQLite only auto-increments a column declared exactly as INTEGER PRIMARY KEY.
BIG_PK = BigInteger().with_variant(Integer, "sqlite")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True, index=True)
    model: Mapped[str] = mapped_column(String(64), index=True)
    model_version: Mapped[str | None] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=5)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    degraded: Mapped[bool] = mapped_column(Boolean, default=False)
    queue_ms: Mapped[float | None] = mapped_column(Float)
    compute_ms: Mapped[float | None] = mapped_column(Float)
    total_ms: Mapped[float | None] = mapped_column(Float)
    tenant: Mapped[str] = mapped_column(String(64), default="public", index=True)
    trace_id: Mapped[str | None] = mapped_column(String(48), index=True)
    worker_id: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(32), default="api")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index("ix_jobs_status_created", Job.status, Job.created_at)


class OutboxEvent(Base):
    __tablename__ = "outbox"

    id: Mapped[int] = mapped_column(BIG_PK, primary_key=True, autoincrement=True)
    topic: Mapped[str] = mapped_column(String(64), index=True)
    message_key: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict] = mapped_column(JSON)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BrokerMessage(Base):
    """Backing store for the embedded DB broker (no Kafka required)."""

    __tablename__ = "broker_messages"

    id: Mapped[int] = mapped_column(BIG_PK, primary_key=True, autoincrement=True)
    topic: Mapped[str] = mapped_column(String(64), index=True)
    message_key: Mapped[str] = mapped_column(String(128), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumer: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


Index("ix_broker_poll", BrokerMessage.topic, BrokerMessage.status, BrokerMessage.available_at)


class ProcessedMessage(Base):
    __tablename__ = "processed_messages"

    message_key: Mapped[str] = mapped_column(String(160), primary_key=True)
    job_id: Mapped[str | None] = mapped_column(String(48), index=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class JobEvent(Base):
    """Append-only audit log; also the tail that feeds the live SSE stream."""

    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(BIG_PK, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(48), index=True)
    type: Mapped[str] = mapped_column(String(32), index=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class DeadLetter(Base):
    __tablename__ = "dead_letters"

    id: Mapped[int] = mapped_column(BIG_PK, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(48), index=True)
    topic: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    error: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    replayed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChaosFault(Base):
    __tablename__ = "chaos_faults"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    target: Mapped[str] = mapped_column(String(24), index=True)   # gateway|worker|db|broker|model
    mode: Mapped[str] = mapped_column(String(24))                 # latency|error|drop|pause|crash
    probability: Mapped[float] = mapped_column(Float, default=1.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str | None] = mapped_column(String(200))
    created_by: Mapped[str] = mapped_column(String(48), default="operator")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"

    worker_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    hostname: Mapped[str] = mapped_column(String(120), default="")
    version: Mapped[str] = mapped_column(String(32), default="")
    models: Mapped[dict] = mapped_column(JSON, default=dict)
    inflight: Mapped[int] = mapped_column(Integer, default=0)
    processed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[str] = mapped_column(String(16), default="healthy")
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class LoadRun(Base):
    __tablename__ = "load_runs"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    scenario: Mapped[str] = mapped_column(String(48), index=True)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    timeline: Mapped[dict] = mapped_column(JSON, default=dict)
    verdict: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
