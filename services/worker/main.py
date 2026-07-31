"""AegisFlow inference worker.

Consumes the request topic, runs the model and writes the terminal state.

Guarantees implemented here
---------------------------
* effectively-once processing - inbox/dedupe table checked before work, written
  in the same transaction as the terminal state;
* bounded concurrency (bulkhead) and per-job timeouts;
* exponential backoff with jitter, capped attempts, then dead-letter;
* leases: a hard-killed worker loses nothing, the relay reaper requeues the
  message once the visibility timeout expires;
* graceful shutdown: SIGTERM stops polling and drains in-flight work.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import socket
import sys
import time
from typing import Any

import uvicorn
from fastapi import FastAPI, Response

from aegisflow_core import jobs as jobsvc
from aegisflow_core.broker import Message, get_broker
from aegisflow_core.chaos import ChaosDrop, chaos
from aegisflow_core.config import settings
from aegisflow_core.db import dispose, init_db
from aegisflow_core.inference import registry
from aegisflow_core.observability import log_event, render_metrics, service_up, setup_logging, worker_inflight
from aegisflow_core.resilience import Bulkhead, PermanentError, TransientError, backoff_delay, with_timeout

settings.service_name = "worker"
log = setup_logging()

WORKER_ID = os.environ.get("WORKER_ID") or f"{settings.instance_id}-{os.getpid()}"


class InferenceWorker:
    def __init__(self) -> None:
        self.broker = get_broker(WORKER_ID)
        # The DB broker can be acked inside the same transaction as the result.
        self.atomic_ack = self.broker.kind == "db"
        self.bulkhead = Bulkhead(settings.worker_concurrency, name="model")
        self._inflight_tasks: set[asyncio.Task] = set()
        self.stopping = asyncio.Event()
        self.processed = 0
        self.failed = 0
        self.retried = 0
        self.started_at = time.time()
        self.last_poll_at = 0.0
        self.state = "starting"

    # ---- lifecycle ------------------------------------------------------
    async def start(self) -> None:
        await init_db()
        registry.load()
        registry.warmup()
        await self.broker.start()
        service_up.labels(service="worker", instance=WORKER_ID).set(1)
        self.state = "healthy"
        log_event(
            log,
            "info",
            "worker ready",
            worker_id=WORKER_ID,
            broker=self.broker.kind,
            concurrency=settings.worker_concurrency,
            models=[m["name"] for m in registry.catalog() if m["loaded"]] or ["fallback-only"],
        )

    async def stop(self) -> None:
        self.stopping.set()
        self.state = "draining"
        deadline = time.time() + 20
        while self.bulkhead.inflight and time.time() < deadline:
            await asyncio.sleep(0.2)
        await self.broker.stop()
        await dispose()
        service_up.labels(service="worker", instance=WORKER_ID).set(0)
        log_event(log, "info", "worker stopped", processed=self.processed, failed=self.failed)

    # ---- loops ----------------------------------------------------------
    async def consume_loop(self) -> None:
        while not self.stopping.is_set():
            try:
                if await chaos.is_paused("worker"):
                    self.state = "paused (chaos)"
                    await asyncio.sleep(1.0)
                    continue
                self.state = "healthy"
                capacity = min(settings.worker_prefetch, self.bulkhead.available)
                if capacity <= 0:
                    await asyncio.sleep(0.05)
                    continue
                messages = await self.broker.poll(settings.topic_requests, max_messages=capacity, wait_s=1.0)
                self.last_poll_at = time.time()
                for message in messages:
                    # Keep a strong reference so in-flight work cannot be
                    # garbage-collected mid-processing.
                    task = asyncio.create_task(self.handle(message))
                    self._inflight_tasks.add(task)
                    task.add_done_callback(self._inflight_tasks.discard)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.state = "broker error"
                log_event(log, "error", "poll failed", error=str(exc))
                await asyncio.sleep(1.0)

    async def heartbeat_loop(self) -> None:
        while not self.stopping.is_set():
            worker_inflight.labels(worker=WORKER_ID).set(self.bulkhead.inflight)
            with contextlib.suppress(Exception):
                await jobsvc.heartbeat(
                    worker_id=WORKER_ID,
                    hostname=socket.gethostname(),
                    models={m["name"]: m["loaded"] for m in registry.catalog()},
                    inflight=self.bulkhead.inflight,
                    processed=self.processed,
                    failed=self.failed,
                    state=self.state,
                )
            await asyncio.sleep(settings.heartbeat_interval_s)

    # ---- one message ----------------------------------------------------
    async def handle(self, message: Message) -> None:
        job_id = str(message.payload.get("job_id", ""))
        model = str(message.payload.get("model", "sentiment-v1"))
        attempt = max(1, message.attempt)

        ack_id = int(message.handle) if self.atomic_ack else None

        if not job_id:
            await self.broker.ack(message)
            return

        if not self.atomic_ack and await jobsvc.already_processed(message.dedupe_key):
            # Duplicate delivery: the result is already durable, just ack.
            await self.broker.ack(message)
            return

        async with self.bulkhead:
            try:
                await chaos.apply("worker")
                await chaos.apply("db")

                model_input = message.payload.get("input")
                if settings.track_running_state or model_input is None:
                    current = await jobsvc.mark_running(job_id, WORKER_ID, attempt)
                    if current is None:
                        await self.broker.ack(message)
                        return
                    if current["status"] in jobsvc.TERMINAL:
                        await self.broker.ack(message)
                        return
                    model_input = current["input"]

                await chaos.apply("model")
                prediction = await with_timeout(
                    asyncio.to_thread(registry.predict, model, model_input),
                    settings.job_timeout_ms,
                    f"inference:{model}",
                )
                await jobsvc.finish_job(
                    job_id=job_id,
                    status="succeeded",
                    dedupe_key=message.dedupe_key,
                    result=prediction.result,
                    model_version=prediction.model_version,
                    degraded=prediction.degraded,
                    compute_ms=prediction.compute_ms,
                    worker_id=WORKER_ID,
                    attempt=attempt,
                    ack_message_id=ack_id,
                )
                if not self.atomic_ack:
                    await self.broker.ack(message)
                self.processed += 1

            except ChaosDrop as exc:
                # Simulated message loss: do not ack, let the lease expire.
                log_event(log, "warning", "message dropped by chaos", job_id=job_id, error=str(exc))

            except PermanentError as exc:
                await jobsvc.finish_job(
                    job_id=job_id,
                    status="failed",
                    dedupe_key=message.dedupe_key,
                    error=f"permanent: {exc}",
                    worker_id=WORKER_ID,
                    attempt=attempt,
                    ack_message_id=ack_id,
                )
                if not self.atomic_ack:
                    await self.broker.ack(message)
                self.failed += 1
                log_event(log, "warning", "job rejected", job_id=job_id, error=str(exc))

            except (TransientError, Exception) as exc:
                if attempt < settings.max_attempts:
                    delay = backoff_delay(attempt)
                    await jobsvc.record_retry(job_id, attempt, type(exc).__name__, model, delay)
                    payload = dict(message.payload)
                    payload["attempt"] = attempt
                    message.payload = payload
                    await self.broker.nack(message, delay_s=delay)
                    self.retried += 1
                    log_event(
                        log, "warning", "job retry scheduled",
                        job_id=job_id, attempt=attempt, retry_in_s=round(delay, 3), error=str(exc),
                    )
                else:
                    await jobsvc.finish_job(
                        job_id=job_id,
                        status="dlq",
                        dedupe_key=message.dedupe_key,
                        error=f"exhausted {attempt} attempts: {exc}",
                        worker_id=WORKER_ID,
                        attempt=attempt,
                        dlq_payload={"job_id": job_id, "model": model, "attempts": attempt, "error": str(exc)},
                        ack_message_id=ack_id,
                    )
                    if not self.atomic_ack:
                        await self.broker.ack(message)
                    self.failed += 1
                    log_event(log, "error", "job dead-lettered", job_id=job_id, attempts=attempt, error=str(exc))

    def snapshot(self) -> dict[str, Any]:
        return {
            "worker_id": WORKER_ID,
            "state": self.state,
            "inflight": self.bulkhead.inflight,
            "concurrency": self.bulkhead.limit,
            "processed": self.processed,
            "retried": self.retried,
            "failed": self.failed,
            "uptime_s": round(time.time() - self.started_at, 1),
            "broker": self.broker.kind,
        }


worker = InferenceWorker()
admin = FastAPI(title="AegisFlow Worker", version=settings.version, docs_url=None, openapi_url=None)


@admin.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", **worker.snapshot()}


@admin.get("/health/ready")
async def ready(response: Response) -> dict[str, Any]:
    healthy = worker.state in ("healthy", "paused (chaos)", "draining")
    response.status_code = 200 if healthy else 503
    return {"status": worker.state, **worker.snapshot()}


@admin.get("/metrics")
async def metrics() -> Response:
    payload, content_type = render_metrics()
    return Response(content=payload, media_type=content_type)


async def main() -> None:
    await worker.start()

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
        asyncio.create_task(worker.consume_loop(), name="consume"),
        asyncio.create_task(worker.heartbeat_loop(), name="heartbeat"),
        asyncio.create_task(server.serve(), name="admin-api"),
    ]
    await stop_event.wait()
    log_event(log, "info", "shutdown signal received, draining")
    server.should_exit = True
    await worker.stop()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
