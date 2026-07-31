"""Structured logging + Prometheus instrumentation."""

from __future__ import annotations

import json
import logging
import sys
import time
from contextvars import ContextVar
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from prometheus_client.exposition import CONTENT_TYPE_LATEST

from .config import settings

trace_id_var: ContextVar[str] = ContextVar("trace_id", default="-")

REGISTRY = CollectorRegistry(auto_describe=True)

LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

http_requests = Counter(
    "aegisflow_http_requests_total", "HTTP requests", ["service", "route", "method", "status"], registry=REGISTRY
)
http_latency = Histogram(
    "aegisflow_http_request_seconds", "HTTP latency", ["service", "route"], buckets=LATENCY_BUCKETS, registry=REGISTRY
)
jobs_submitted = Counter(
    "aegisflow_jobs_submitted_total", "Jobs accepted by the gateway", ["model", "source"], registry=REGISTRY
)
jobs_completed = Counter(
    "aegisflow_jobs_completed_total", "Jobs finished by workers", ["model", "status"], registry=REGISTRY
)
job_compute = Histogram(
    "aegisflow_job_compute_seconds", "Model compute time", ["model"], buckets=LATENCY_BUCKETS, registry=REGISTRY
)
job_e2e = Histogram(
    "aegisflow_job_end_to_end_seconds", "Submit -> result", ["model"], buckets=LATENCY_BUCKETS, registry=REGISTRY
)
job_retries = Counter("aegisflow_job_retries_total", "Retry attempts", ["model", "reason"], registry=REGISTRY)
dead_letters = Counter("aegisflow_dead_letters_total", "Messages parked in the DLQ", ["model"], registry=REGISTRY)
queue_depth = Gauge("aegisflow_queue_depth", "Messages waiting to be processed", registry=REGISTRY)
outbox_pending = Gauge("aegisflow_outbox_pending", "Unpublished outbox rows", registry=REGISTRY)
outbox_published = Counter("aegisflow_outbox_published_total", "Outbox rows published", ["topic"], registry=REGISTRY)
worker_inflight = Gauge("aegisflow_worker_inflight", "In-flight jobs", ["worker"], registry=REGISTRY)
breaker_state = Gauge("aegisflow_circuit_breaker_state", "0 closed / 1 half-open / 2 open", ["name"], registry=REGISTRY)
chaos_active = Gauge("aegisflow_chaos_faults_active", "Active injected faults", ["target"], registry=REGISTRY)
cache_events = Counter("aegisflow_cache_events_total", "Cache hits and misses", ["event"], registry=REGISTRY)
rate_limited = Counter("aegisflow_rate_limited_total", "Requests rejected by the limiter", registry=REGISTRY)
broker_publish = Counter("aegisflow_broker_publish_total", "Broker publishes", ["topic", "result"], registry=REGISTRY)
service_up = Gauge("aegisflow_service_up", "1 when the process is serving", ["service", "instance"], registry=REGISTRY)


def render_metrics() -> tuple[bytes, str]:
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "service": settings.service_name,
            "instance": settings.instance_id,
            "logger": record.name,
            "msg": record.getMessage(),
            "trace_id": trace_id_var.get(),
        }
        extra = getattr(record, "extra_fields", None)
        if extra:
            payload.update(extra)
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging() -> logging.Logger:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    if settings.log_json:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s :: %(message)s"))
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())
    for noisy in ("uvicorn.access", "aiokafka", "sqlalchemy.engine.Engine", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel("WARNING")
    return logging.getLogger(f"aegisflow.{settings.service_name}")


def log_event(logger: logging.Logger, level: str, message: str, **fields: Any) -> None:
    logger.log(getattr(logging, level.upper(), logging.INFO), message, extra={"extra_fields": fields})
