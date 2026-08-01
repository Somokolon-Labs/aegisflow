"""AegisFlow gateway - the only service exposed to the internet.

Responsibilities: authentication, rate limiting, request validation, durable
enqueue (transactional outbox), read APIs, live event stream, operator APIs for
chaos + DLQ, and a thin reverse proxy to the resilience lab.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import APIKeyHeader

from aegisflow_core import jobs as jobsvc
from aegisflow_core.broker import get_broker
from aegisflow_core.cache import cache
from aegisflow_core.chaos import chaos
from aegisflow_core.config import settings
from aegisflow_core.db import dispose, init_db, ping
from aegisflow_core.inference import registry
from aegisflow_core.observability import (
    http_latency,
    http_requests,
    log_event,
    render_metrics,
    service_up,
    setup_logging,
    trace_id_var,
)
from aegisflow_core.ratelimit import limiter
from aegisflow_core.resilience import PermanentError, TransientError
from aegisflow_core.schemas import (
    BatchPredictRequest,
    ChaosRequest,
    HealthResponse,
    PredictRequest,
    SubmitResponse,
)

settings.service_name = "gateway"
log = setup_logging()

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
STARTED_AT = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await cache.connect()
    await limiter.connect()
    registry.load()
    broker = get_broker(f"gateway-{settings.instance_id}")
    try:
        await broker.start()
    except Exception as exc:
        log_event(log, "error", "broker start failed, relying on outbox", error=str(exc))
    service_up.labels(service="gateway", instance=settings.instance_id).set(1)

    # Optional single-process mode: run the worker and relay loops here instead
    # of as separate deployments. Same code, same guarantees, one container.
    embedded: list[asyncio.Task] = []
    embedded_worker = None
    embedded_relay = None
    if settings.embedded_worker:
        from services.relay.main import Relay
        from services.worker.main import InferenceWorker

        settings.service_name = "gateway"  # the imports above claim the name
        embedded_worker = InferenceWorker()
        await embedded_worker.start()
        embedded_relay = Relay()
        await embedded_relay.start()
        embedded = [
            asyncio.create_task(embedded_worker.consume_loop(), name="embedded-consume"),
            asyncio.create_task(embedded_worker.heartbeat_loop(), name="embedded-heartbeat"),
            asyncio.create_task(embedded_relay.drain_outbox(), name="embedded-outbox"),
            asyncio.create_task(embedded_relay.reap_loop(), name="embedded-reaper"),
            asyncio.create_task(embedded_relay.janitor_loop(), name="embedded-janitor"),
        ]

    log_event(
        log,
        "info",
        "gateway ready",
        broker=broker.kind,
        database="sqlite" if settings.is_sqlite else "postgres",
        cache=cache.status()["backend"],
        models=[m["name"] for m in registry.catalog()],
        embedded_worker=settings.embedded_worker,
    )
    try:
        yield
    finally:
        service_up.labels(service="gateway", instance=settings.instance_id).set(0)
        if embedded_worker is not None:
            await embedded_worker.stop()
        if embedded_relay is not None:
            await embedded_relay.stop()
        for task in embedded:
            task.cancel()
        if embedded:
            await asyncio.gather(*embedded, return_exceptions=True)
        await broker.stop()
        await cache.close()
        await dispose()


app = FastAPI(
    title="AegisFlow Gateway",
    version=settings.version,
    description=(
        "Event-driven ML inference platform. Requests are durably enqueued via a transactional "
        "outbox, processed by an elastic worker fleet with retries, dead-lettering and circuit "
        "breakers, and observed through Prometheus metrics and a live event stream."
    ),
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Trace-Id", "X-RateLimit-Remaining"],
)


# --------------------------------------------------------------------------
# middleware
# --------------------------------------------------------------------------
@app.middleware("http")
async def observability_middleware(request: Request, call_next):
    trace_id = request.headers.get("X-Trace-Id") or uuid.uuid4().hex[:16]
    token = trace_id_var.set(trace_id)
    route = request.scope.get("path", "unknown")
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        http_requests.labels(service="gateway", route=route, method=request.method, status="500").inc()
        trace_id_var.reset(token)
        raise
    elapsed = time.perf_counter() - started
    http_latency.labels(service="gateway", route=route).observe(elapsed)
    http_requests.labels(service="gateway", route=route, method=request.method, status=str(response.status_code)).inc()
    response.headers["X-Trace-Id"] = trace_id
    response.headers["X-Response-Time-Ms"] = f"{elapsed * 1000:.2f}"
    trace_id_var.reset(token)
    return response


# --------------------------------------------------------------------------
# dependencies
# --------------------------------------------------------------------------
async def require_api_key(key: str | None = Depends(api_key_header)) -> str:
    if not settings.require_api_key:
        return key or "anonymous"
    if key and (key in settings.api_key_set or key == settings.admin_api_key):
        return key
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing or invalid X-API-Key")


async def require_admin(key: str | None = Depends(api_key_header)) -> str:
    if not settings.require_api_key:
        return key or "anonymous"
    if key == settings.admin_api_key:
        return key
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin key required")


async def enforce_limits(request: Request, key: str = Depends(require_api_key)) -> str:
    decision = await limiter.check(key or request.client.host if request.client else "anonymous")
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="rate limit exceeded, retry shortly",
            headers={**decision.headers(), "Retry-After": "1"},
        )
    # Gateway-level chaos (latency / error injection at the edge).
    await chaos.apply("gateway")
    return key


# --------------------------------------------------------------------------
# meta
# --------------------------------------------------------------------------
@app.get("/", tags=["meta"])
async def root() -> dict[str, Any]:
    return {
        "name": "AegisFlow",
        "component": "gateway",
        "version": settings.version,
        "uptime_s": round(time.time() - STARTED_AT, 1),
        "broker": get_broker().kind,
        "docs": "/docs",
        "endpoints": ["/v1/predict", "/v1/jobs", "/v1/events", "/v1/stats", "/v1/chaos", "/v1/dlq", "/metrics"],
    }


@app.get("/health", response_model=HealthResponse, tags=["meta"])
async def health() -> HealthResponse:
    return HealthResponse(status="ok", service="gateway", version=settings.version, checks={"process": "alive"})


@app.get("/health/ready", response_model=HealthResponse, tags=["meta"])
async def ready(response: Response) -> HealthResponse:
    db_ok = await ping()
    broker_ok = await get_broker().healthy()
    ok = db_ok  # broker outages are tolerated: the outbox keeps accepting work
    response.status_code = status.HTTP_200_OK if ok else status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status="ready" if ok else "degraded",
        service="gateway",
        version=settings.version,
        checks={
            "database": db_ok,
            "broker": broker_ok,
            "cache": cache.status(),
            "models_loaded": sum(1 for m in registry.catalog() if m["loaded"]),
        },
    )


@app.get("/metrics", tags=["meta"])
async def metrics() -> Response:
    payload, content_type = render_metrics()
    return Response(content=payload, media_type=content_type)


# --------------------------------------------------------------------------
# inference
# --------------------------------------------------------------------------
@app.post("/v1/predict", response_model=SubmitResponse, status_code=202, tags=["inference"])
async def predict(
    body: PredictRequest,
    request: Request,
    response: Response,
    key: str = Depends(enforce_limits),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> SubmitResponse:
    try:
        view, deduped = await jobsvc.submit_job(
            model=body.model,
            payload=body.input,
            priority=body.priority,
            idempotency_key=body.idempotency_key or idempotency_key,
            tenant=body.tenant,
            source="api",
            trace_id=trace_id_var.get(),
        )
    except PermanentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except TransientError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    wait_ms = min(body.wait_ms, settings.sync_wait_ms_max)
    if wait_ms and not deduped:
        settled = await jobsvc.wait_for_result(view["id"], wait_ms)
        view = settled or view
    if view["status"] in jobsvc.TERMINAL:
        response.status_code = 200
    return SubmitResponse(job=view, deduplicated=deduped)


@app.post("/v1/predict/batch", tags=["inference"])
async def predict_batch(body: BatchPredictRequest, key: str = Depends(enforce_limits)) -> dict[str, Any]:
    if not body.inputs:
        raise HTTPException(status_code=422, detail="inputs must not be empty")
    accepted: list[dict[str, Any]] = []
    for item in body.inputs:
        try:
            view, _ = await jobsvc.submit_job(
                model=body.model, payload=item, priority=body.priority, tenant=body.tenant, source="batch"
            )
            accepted.append(view)
        except PermanentError as exc:
            accepted.append({"error": str(exc), "input": item})
    return {"accepted": len(accepted), "jobs": accepted}


@app.get("/v1/jobs/{job_id}", tags=["inference"])
async def get_job(job_id: str, key: str = Depends(require_api_key)) -> dict[str, Any]:
    view = await jobsvc.get_job(job_id)
    if view is None:
        raise HTTPException(status_code=404, detail="job not found")
    return view


@app.get("/v1/jobs", tags=["inference"])
async def list_jobs(
    key: str = Depends(require_api_key),
    job_status: str | None = Query(default=None, alias="status"),
    model: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    rows = await jobsvc.list_jobs(status=job_status, model=model, limit=limit)
    return {"count": len(rows), "jobs": rows}


@app.get("/v1/models", tags=["inference"])
async def models() -> dict[str, Any]:
    return {"models": registry.catalog()}


# --------------------------------------------------------------------------
# observability
# --------------------------------------------------------------------------
@app.get("/v1/stats", tags=["observability"])
async def platform_stats(window_minutes: int = Query(default=15, ge=1, le=1440)) -> dict[str, Any]:
    return await jobsvc.stats(window_minutes)


@app.get("/v1/events", tags=["observability"])
async def event_stream(after: int = Query(default=0, ge=0), request: Request = None) -> StreamingResponse:  # type: ignore[assignment]
    """Server-sent events tailing the append-only job event log."""

    async def generator():
        cursor = after or max(0, await jobsvc.latest_event_id() - 25)
        yield f"event: cursor\ndata: {json.dumps({'cursor': cursor})}\n\n"
        idle = 0
        while True:
            if request is not None and await request.is_disconnected():
                break
            batch = await jobsvc.tail_events(cursor, limit=200)
            if batch:
                cursor = batch[-1]["id"]
                idle = 0
                for event in batch:
                    yield f"event: job\ndata: {json.dumps(event, default=str)}\n\n"
            else:
                idle += 1
                if idle % 10 == 0:
                    yield ": keep-alive\n\n"
            await asyncio.sleep(0.6)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


# --------------------------------------------------------------------------
# operations: dead letters + chaos
# --------------------------------------------------------------------------
@app.get("/v1/dlq", tags=["operations"])
async def dlq(limit: int = Query(default=50, ge=1, le=200), key: str = Depends(require_api_key)) -> dict[str, Any]:
    rows = await jobsvc.list_dead_letters(limit=limit)
    return {"count": len(rows), "dead_letters": rows}


@app.post("/v1/dlq/{dlq_id}/replay", tags=["operations"])
async def replay(dlq_id: int, key: str = Depends(require_admin)) -> dict[str, Any]:
    try:
        return await jobsvc.replay_dead_letter(dlq_id)
    except PermanentError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/v1/dlq/replay-all", tags=["operations"])
async def replay_all(key: str = Depends(require_admin)) -> dict[str, Any]:
    replayed = await jobsvc.replay_all_dead_letters()
    return {"replayed": len(replayed), "job_ids": replayed}


@app.get("/v1/chaos", tags=["operations"])
async def chaos_list() -> dict[str, Any]:
    return {"active": await chaos.list_active(force=True), "history": await chaos.history(limit=20)}


@app.post("/v1/chaos", tags=["operations"])
async def chaos_inject(body: ChaosRequest, key: str = Depends(require_admin)) -> dict[str, Any]:
    try:
        fault = await chaos.inject(
            body.target,
            body.mode,
            probability=body.probability,
            latency_ms=body.latency_ms,
            ttl_s=body.ttl_s,
            note=body.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    log_event(log, "warning", "chaos fault injected", **fault)
    return fault


@app.delete("/v1/chaos", tags=["operations"])
async def chaos_clear(target: str | None = None, key: str = Depends(require_admin)) -> dict[str, Any]:
    cleared = await chaos.clear(target=target)
    return {"cleared": cleared, "target": target or "all"}


# --------------------------------------------------------------------------
# resilience lab proxy (keeps the browser on a single origin)
# --------------------------------------------------------------------------
@app.api_route("/v1/lab/{path:path}", methods=["GET", "POST", "DELETE"], tags=["operations"])
async def lab_proxy(path: str, request: Request) -> Response:
    url = f"{settings.lab_url.rstrip('/')}/v1/{path}"
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            upstream = await client.request(
                request.method,
                url,
                content=body or None,
                params=dict(request.query_params),
                headers={"content-type": request.headers.get("content-type", "application/json")},
            )
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/json"),
        )
    except httpx.HTTPError as exc:
        return JSONResponse(
            status_code=503,
            content={"detail": "resilience lab unreachable", "error": str(exc), "lab_url": settings.lab_url},
        )


@app.exception_handler(PermanentError)
async def permanent_handler(_request: Request, exc: PermanentError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(TransientError)
async def transient_handler(_request: Request, exc: TransientError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)}, headers={"Retry-After": "1"})
