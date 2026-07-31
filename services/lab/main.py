"""AegisFlow resilience lab.

Runs a paced load test against the gateway, injects a real fault in the middle
of it, then reports the numbers that matter: sustained throughput, latency
percentiles, recovery time after the fault clears, and whether a single
accepted job was lost.

Every job submitted by a run is tagged with the run id as its tenant, so
accounting is exact rather than estimated.
"""

from __future__ import annotations

import asyncio
import contextlib
import statistics
import time
from datetime import timedelta
from typing import Any

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select

from aegisflow_core.chaos import chaos
from aegisflow_core.config import settings
from aegisflow_core.db import dispose, init_db, iso, session_scope
from aegisflow_core.models import DeadLetter, Job, JobEvent, LoadRun, new_id, utcnow
from aegisflow_core.observability import log_event, render_metrics, service_up, setup_logging
from aegisflow_core.schemas import LoadTestRequest

settings.service_name = "lab"
log = setup_logging()

SAMPLE_TEXTS = [
    "the courier arrived early and the packaging was perfect",
    "absolutely terrible support, three weeks and no refund",
    "fabric quality is decent for the price, nothing special",
    "app crashes every time i open the checkout page",
    "fast delivery, great communication, would order again",
    "the colour faded after one wash, very disappointed",
    "works exactly as described, no complaints at all",
    "delivery was late but the product itself is fine",
]

SCENARIOS: dict[str, dict[str, Any]] = {
    "baseline": {
        "title": "Baseline throughput",
        "detail": "No faults. Establishes the reference numbers for latency and throughput.",
        "fault": None,
    },
    "worker-loss": {
        "title": "Worker fleet loss",
        "detail": "All consumers stop mid-load. Leases expire, the relay requeues, the fleet catches up.",
        "fault": {"target": "worker", "mode": "pause", "probability": 1.0, "latency_ms": 0},
    },
    "broker-outage": {
        "title": "Broker outage",
        "detail": "Publishing fails. The gateway keeps accepting because the outbox is durable.",
        "fault": {"target": "broker", "mode": "error", "probability": 1.0, "latency_ms": 0},
    },
    "db-slowdown": {
        "title": "Database slowdown",
        "detail": "800ms of injected storage latency in the worker path; timeouts and retries absorb it.",
        "fault": {"target": "db", "mode": "latency", "probability": 0.8, "latency_ms": 800},
    },
    "poison-payloads": {
        "title": "Poison payloads",
        "detail": "30% of model calls fail hard. Retries, then dead-letter, without stalling the queue.",
        "fault": {"target": "model", "mode": "error", "probability": 0.3, "latency_ms": 0},
    },
    "burst": {
        "title": "Traffic burst",
        "detail": "3x request rate for the fault window with no injected failure - pure elasticity test.",
        "fault": None,
    },
}

_running: dict[str, asyncio.Task] = {}


# --------------------------------------------------------------------------
# load engine
# --------------------------------------------------------------------------
class RunRecorder:
    def __init__(self) -> None:
        self.samples: list[tuple[float, float, bool, int]] = []  # offset, latency_ms, ok, status
        self.accepted = 0
        self.rejected = 0

    def add(self, offset: float, latency_ms: float, ok: bool, status_code: int) -> None:
        self.samples.append((offset, latency_ms, ok, status_code))
        if ok:
            self.accepted += 1
        else:
            self.rejected += 1

    def client_timeline(self) -> list[dict[str, Any]]:
        buckets: dict[int, list[tuple[float, bool]]] = {}
        for offset, latency, ok, _status in self.samples:
            buckets.setdefault(int(offset), []).append((latency, ok))
        timeline = []
        for second in sorted(buckets):
            rows = buckets[second]
            latencies = [r[0] for r in rows]
            ok_count = sum(1 for r in rows if r[1])
            timeline.append(
                {
                    "t": second,
                    "sent": len(rows),
                    "accepted": ok_count,
                    "errors": len(rows) - ok_count,
                    "p95_ms": _pct(latencies, 0.95),
                    "avg_ms": round(statistics.fmean(latencies), 2) if latencies else None,
                }
            )
        return timeline

    def status_codes(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for _o, _l, _ok, code in self.samples:
            out[str(code)] = out.get(str(code), 0) + 1
        return out


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return round(ordered[idx], 2)


async def _submit_one(client: httpx.AsyncClient, body: dict, recorder: RunRecorder, started: float) -> None:
    offset = time.perf_counter() - started
    request_started = time.perf_counter()
    try:
        response = await client.post("/v1/predict", json=body)
        latency = (time.perf_counter() - request_started) * 1000
        recorder.add(offset, latency, response.status_code in (200, 202), response.status_code)
    except Exception:
        latency = (time.perf_counter() - request_started) * 1000
        recorder.add(offset, latency, False, 0)


async def _generate_load(run_id: str, request: LoadTestRequest, recorder: RunRecorder) -> None:
    scenario = SCENARIOS[request.scenario]
    burst_window = (request.fault_at_s, request.fault_at_s + request.fault_duration_s)
    headers = {"X-API-Key": sorted(settings.api_key_set)[0] if settings.api_key_set else "demo-key-aegisflow"}
    limits = httpx.Limits(max_connections=request.concurrency, max_keepalive_connections=request.concurrency)
    started = time.perf_counter()
    semaphore = asyncio.Semaphore(request.concurrency)
    inflight: set[asyncio.Task] = set()

    async with httpx.AsyncClient(
        base_url=settings.gateway_url, headers=headers, timeout=10.0, limits=limits
    ) as client:

        async def _guarded(body: dict) -> None:
            async with semaphore:
                await _submit_one(client, body, recorder, started)

        index = 0
        next_slot = time.perf_counter()
        while True:
            elapsed = time.perf_counter() - started
            if elapsed >= request.duration_s:
                break
            rate = request.rps
            if request.scenario == "burst" and burst_window[0] <= elapsed < burst_window[1]:
                rate = request.rps * 3
            interval = 1.0 / max(1, rate)
            body = {
                "model": request.model,
                "input": {"text": SAMPLE_TEXTS[index % len(SAMPLE_TEXTS)]},
                "priority": 5,
                "tenant": run_id,
            }
            if request.scenario == "poison-payloads" and index % 7 == 0:
                body["input"] = {"text": ""}  # invalid on purpose -> 422 at the edge
            task = asyncio.create_task(_guarded(body))
            inflight.add(task)
            task.add_done_callback(inflight.discard)
            index += 1
            next_slot += interval
            sleep_for = next_slot - time.perf_counter()
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
            else:
                next_slot = time.perf_counter()
                await asyncio.sleep(0)

        if inflight:
            await asyncio.wait(inflight, timeout=15)
    del scenario


# --------------------------------------------------------------------------
# accounting
# --------------------------------------------------------------------------
async def _tenant_accounting(run_id: str) -> dict[str, Any]:
    async with session_scope() as session:
        rows = (await session.execute(select(Job.status, func.count()).where(Job.tenant == run_id).group_by(Job.status))).all()
        finished = (
            await session.execute(
                select(Job.finished_at, Job.total_ms, Job.compute_ms)
                .where(Job.tenant == run_id, Job.finished_at.is_not(None))
                .order_by(Job.finished_at)
            )
        ).all()
        retries = int(
            await session.scalar(
                select(func.count())
                .select_from(JobEvent)
                .where(JobEvent.type == "retry", JobEvent.job_id.in_(select(Job.id).where(Job.tenant == run_id)))
            )
            or 0
        )
        dlq = int(
            await session.scalar(
                select(func.count())
                .select_from(DeadLetter)
                .where(DeadLetter.job_id.in_(select(Job.id).where(Job.tenant == run_id)))
            )
            or 0
        )
    by_status = {status: int(count) for status, count in rows}
    return {"by_status": by_status, "finished": finished, "retries": retries, "dlq": dlq}


async def _wait_for_drain(run_id: str, timeout_s: float = 90.0) -> tuple[float | None, dict[str, int]]:
    started = time.perf_counter()
    while True:
        account = await _tenant_accounting(run_id)
        pending = account["by_status"].get("queued", 0) + account["by_status"].get("running", 0)
        if pending == 0:
            return round(time.perf_counter() - started, 2), account["by_status"]
        if time.perf_counter() - started > timeout_s:
            return None, account["by_status"]
        await asyncio.sleep(0.5)


def _completion_timeline(finished_rows: list[Any], run_started: Any) -> list[dict[str, Any]]:
    buckets: dict[int, list[float]] = {}
    for finished_at, total_ms, _compute in finished_rows:
        if finished_at is None:
            continue
        stamp = finished_at if finished_at.tzinfo else finished_at.replace(tzinfo=run_started.tzinfo)
        offset = int((stamp - run_started).total_seconds())
        buckets.setdefault(max(0, offset), []).append(float(total_ms or 0))
    return [
        {"t": second, "completed": len(buckets[second]), "p95_ms": _pct(buckets[second], 0.95)}
        for second in sorted(buckets)
    ]


def _recovery_seconds(completion: list[dict[str, Any]], fault_from: int, fault_to: int) -> float | None:
    """Seconds after the fault cleared until completion throughput recovers."""
    pre = [b["completed"] for b in completion if b["t"] < fault_from]
    if not pre:
        return None
    baseline = statistics.median(pre) if pre else 0
    if baseline <= 0:
        return None
    target = max(1.0, baseline * 0.5)
    for bucket in completion:
        if bucket["t"] >= fault_to and bucket["completed"] >= target:
            return float(bucket["t"] - fault_to)
    return None


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------
async def _execute(run_id: str, request: LoadTestRequest) -> None:
    scenario = SCENARIOS[request.scenario]
    recorder = RunRecorder()
    run_started = utcnow()
    fault_id: str | None = None
    events: list[dict[str, Any]] = [{"t": 0, "event": "load started", "detail": f"{request.rps} rps target"}]

    async def _fault_schedule() -> None:
        nonlocal fault_id
        spec = scenario["fault"]
        if not spec:
            return
        await asyncio.sleep(request.fault_at_s)
        fault = await chaos.inject(
            spec["target"],
            spec["mode"],
            probability=spec["probability"],
            latency_ms=spec["latency_ms"],
            ttl_s=request.fault_duration_s,
            note=f"lab run {run_id}",
            created_by="resilience-lab",
        )
        fault_id = fault["id"]
        events.append(
            {
                "t": request.fault_at_s,
                "event": "fault injected",
                "detail": f"{spec['target']}/{spec['mode']} p={spec['probability']}",
            }
        )
        await asyncio.sleep(request.fault_duration_s)
        await chaos.clear(fault_id=fault_id)
        events.append({"t": request.fault_at_s + request.fault_duration_s, "event": "fault cleared", "detail": "recovery observed"})

    try:
        await asyncio.gather(_generate_load(run_id, request, recorder), _fault_schedule())
        events.append({"t": request.duration_s, "event": "load finished", "detail": "waiting for queue drain"})

        drain_seconds, statuses = await _wait_for_drain(run_id)
        account = await _tenant_accounting(run_id)
        completion = _completion_timeline(account["finished"], run_started)

        latencies = [s[1] for s in recorder.samples if s[2]]
        durations = [float(row[1] or 0) for row in account["finished"]]
        terminal = sum(account["by_status"].get(s, 0) for s in ("succeeded", "failed", "dlq"))
        pending = account["by_status"].get("queued", 0) + account["by_status"].get("running", 0)
        lost = max(0, recorder.accepted - terminal - pending)
        fault_from = request.fault_at_s
        fault_to = request.fault_at_s + request.fault_duration_s
        recovery = _recovery_seconds(completion, fault_from, fault_to) if scenario["fault"] else None

        during = [s for s in recorder.samples if fault_from <= s[0] < fault_to]
        during_errors = sum(1 for s in during if not s[2])

        metrics = {
            "requests_sent": len(recorder.samples),
            "accepted": recorder.accepted,
            "rejected": recorder.rejected,
            "status_codes": recorder.status_codes(),
            "achieved_rps": round(len(recorder.samples) / max(1, request.duration_s), 2),
            "completed_jobs": terminal,
            "succeeded": account["by_status"].get("succeeded", 0),
            "failed": account["by_status"].get("failed", 0),
            "dlq": account["by_status"].get("dlq", 0),
            "retries": account["retries"],
            "still_pending": pending,
            "submit_latency_ms": {
                "p50": _pct(latencies, 0.5),
                "p95": _pct(latencies, 0.95),
                "p99": _pct(latencies, 0.99),
                "max": round(max(latencies), 2) if latencies else None,
            },
            "end_to_end_ms": {
                "p50": _pct(durations, 0.5),
                "p95": _pct(durations, 0.95),
                "p99": _pct(durations, 0.99),
            },
            "queue_drain_s": drain_seconds,
            "edge_error_rate": round(recorder.rejected / max(1, len(recorder.samples)), 4),
            "edge_error_rate_during_fault": round(during_errors / max(1, len(during)), 4) if during else None,
        }
        verdict = {
            "zero_data_loss": lost == 0,
            "lost_jobs": lost,
            "accepted_jobs": recorder.accepted,
            "accounted_jobs": terminal + pending,
            "recovery_seconds": recovery,
            "availability_during_fault": round(1 - (during_errors / len(during)), 4) if during else None,
            "sustained_rps": metrics["achieved_rps"],
            "p99_submit_ms": metrics["submit_latency_ms"]["p99"],
            "notes": scenario["detail"],
        }

        await _finalise(
            run_id,
            status="completed",
            metrics=metrics,
            timeline={"client": recorder.client_timeline(), "completions": completion, "events": events},
            verdict=verdict,
        )
        log_event(log, "info", "load run finished", run_id=run_id, **verdict)

    except asyncio.CancelledError:
        await _finalise(run_id, status="cancelled", metrics={}, timeline={"events": events}, verdict={})
        raise
    except Exception as exc:  # pragma: no cover - operational safety net
        log_event(log, "error", "load run failed", run_id=run_id, error=str(exc))
        await _finalise(run_id, status="failed", metrics={}, timeline={"events": events}, verdict={}, error=str(exc))
    finally:
        if fault_id:
            with contextlib.suppress(Exception):
                await chaos.clear(fault_id=fault_id)
        _running.pop(run_id, None)


async def _finalise(
    run_id: str,
    *,
    status: str,
    metrics: dict,
    timeline: dict,
    verdict: dict,
    error: str | None = None,
) -> None:
    async with session_scope() as session:
        run = (await session.execute(select(LoadRun).where(LoadRun.id == run_id))).scalar_one_or_none()
        if run is None:
            return
        run.status = status
        run.metrics = metrics
        run.timeline = timeline  # type: ignore[assignment]
        run.verdict = verdict
        run.error = error
        run.finished_at = utcnow()


def _run_view(run: LoadRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "scenario": run.scenario,
        "title": SCENARIOS.get(run.scenario, {}).get("title", run.scenario),
        "params": run.params,
        "status": run.status,
        "metrics": run.metrics,
        "timeline": run.timeline,
        "verdict": run.verdict,
        "error": run.error,
        "started_at": iso(run.started_at),
        "finished_at": iso(run.finished_at),
    }


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
app = FastAPI(
    title="AegisFlow Resilience Lab",
    version=settings.version,
    description="Load generation + fault injection + automated resilience reporting.",
)
app.add_middleware(
    CORSMiddleware, allow_origins=settings.cors_list, allow_methods=["*"], allow_headers=["*"]
)


@app.on_event("startup")
async def _startup() -> None:
    await init_db()
    service_up.labels(service="lab", instance=settings.instance_id).set(1)
    log_event(log, "info", "resilience lab ready", gateway=settings.gateway_url)


@app.on_event("shutdown")
async def _shutdown() -> None:
    for task in list(_running.values()):
        task.cancel()
    await dispose()


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "service": "lab", "active_runs": len(_running), "gateway": settings.gateway_url}


@app.get("/metrics")
async def metrics() -> Response:
    payload, content_type = render_metrics()
    return Response(content=payload, media_type=content_type)


@app.get("/v1/scenarios")
async def scenarios() -> dict[str, Any]:
    return {
        "scenarios": [
            {
                "key": key,
                "title": spec["title"],
                "detail": spec["detail"],
                "fault": spec["fault"],
            }
            for key, spec in SCENARIOS.items()
        ]
    }


@app.post("/v1/loadtest", status_code=202)
async def start_run(request: LoadTestRequest, background: BackgroundTasks) -> dict[str, Any]:
    if len(_running) >= 2:
        raise HTTPException(status_code=429, detail="another run is already in progress")
    if request.scenario not in SCENARIOS:
        raise HTTPException(status_code=422, detail=f"unknown scenario '{request.scenario}'")
    if request.fault_at_s + request.fault_duration_s > request.duration_s:
        raise HTTPException(status_code=422, detail="fault window must finish before the run ends")

    run_id = new_id("run")
    async with session_scope() as session:
        session.add(
            LoadRun(
                id=run_id,
                scenario=request.scenario,
                params=request.model_dump(),
                status="running",
                timeline={"events": []},  # type: ignore[arg-type]
            )
        )

    task = asyncio.create_task(_execute(run_id, request), name=f"loadrun-{run_id}")
    _running[run_id] = task
    return {
        "id": run_id,
        "status": "running",
        "scenario": request.scenario,
        "expected_duration_s": request.duration_s + 5,
        "poll": f"/v1/loadtest/{run_id}",
    }


@app.get("/v1/loadtest")
async def list_runs(limit: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
    async with session_scope() as session:
        rows = (
            await session.execute(select(LoadRun).order_by(LoadRun.started_at.desc()).limit(limit))
        ).scalars().all()
    return {"count": len(rows), "runs": [_run_view(row) for row in rows]}


@app.get("/v1/loadtest/{run_id}")
async def get_run(run_id: str) -> dict[str, Any]:
    async with session_scope() as session:
        run = (await session.execute(select(LoadRun).where(LoadRun.id == run_id))).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return _run_view(run)


@app.delete("/v1/loadtest/{run_id}")
async def cancel_run(run_id: str) -> dict[str, Any]:
    task = _running.get(run_id)
    if task is None:
        raise HTTPException(status_code=404, detail="run is not active")
    task.cancel()
    return {"id": run_id, "status": "cancelling"}


@app.get("/v1/report")
async def report() -> dict[str, Any]:
    """Best numbers achieved per scenario - the source for the README badges."""
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(LoadRun).where(LoadRun.status == "completed").order_by(LoadRun.started_at.desc()).limit(200)
            )
        ).scalars().all()
    best: dict[str, dict[str, Any]] = {}
    for run in rows:
        metrics = run.metrics or {}
        verdict = run.verdict or {}
        current = best.get(run.scenario)
        if not current or (metrics.get("achieved_rps", 0) or 0) > (current["achieved_rps"] or 0):
            best[run.scenario] = {
                "run_id": run.id,
                "achieved_rps": metrics.get("achieved_rps"),
                "p95_submit_ms": (metrics.get("submit_latency_ms") or {}).get("p95"),
                "p99_submit_ms": (metrics.get("submit_latency_ms") or {}).get("p99"),
                "p95_end_to_end_ms": (metrics.get("end_to_end_ms") or {}).get("p95"),
                "completed_jobs": metrics.get("completed_jobs"),
                "zero_data_loss": verdict.get("zero_data_loss"),
                "recovery_seconds": verdict.get("recovery_seconds"),
                "at": iso(run.started_at),
            }
    since = utcnow() - timedelta(days=30)
    async with session_scope() as session:
        total_runs = int(
            await session.scalar(select(func.count()).select_from(LoadRun).where(LoadRun.started_at >= since)) or 0
        )
    return {"runs_last_30d": total_runs, "best_per_scenario": best}
