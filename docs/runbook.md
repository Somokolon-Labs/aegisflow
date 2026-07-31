# Runbook

Operational procedures for AegisFlow. Every check below is available through the API, so it
works the same locally, in compose and in Kubernetes.

## Health checks

| Endpoint | Meaning |
| --- | --- |
| `GET /health` | process is alive (liveness) |
| `GET /health/ready` | database reachable, models loaded (readiness). Broker outages do **not** make the gateway unready — ingest survives them |
| `GET /v1/stats` | throughput, latency percentiles, queue, workers, breaker, DLQ |
| `GET /metrics` | Prometheus exposition, every service |
| `:9101/health` | worker state, in-flight, processed, failed |
| `:9102/health` | relay: published, deferred, requeued, breaker |

## Triage

### Queue depth is climbing

1. `GET /v1/stats` → compare `throughput.per_second` with the arrival rate.
2. Check `workers[]`: are any online, and what is `state`?
3. If workers are healthy and saturated, scale them:
   `kubectl scale deploy/worker --replicas=8 -n aegisflow`.
4. If `queue.oldest_pending_age_s` grows while completions stay flat, look for a stuck lease —
   the relay reaper logs `requeued messages from expired leases`.

### Outbox is not draining (`queue.outbox_pending > 0`)

The broker is unreachable or the relay is down. Ingest is unaffected.

1. `curl :9102/health` → `breaker.state` should be `open` if publishing fails.
2. Verify broker connectivity (`kafka_bootstrap` / `redis_url`).
3. Once the broker returns, the relay drains automatically; no manual replay needed.
4. If the relay pod is gone: `kubectl rollout restart deploy/relay -n aegisflow`.

### Jobs are failing

1. `GET /v1/jobs?status=failed&limit=20` — `permanent:` prefixed errors are client input
   problems, not platform problems.
2. `GET /v1/dlq` for exhausted jobs. Fix the cause, then replay:
   - one: `POST /v1/dlq/{id}/replay`
   - all: `POST /v1/dlq/replay-all`
3. Replays are safe: the job is reset and re-queued, and the dedupe table prevents double
   results.

### Latency regression

1. `latency_ms.compute_p95` high → model or CPU limits.
2. `latency_ms.queue_p95` high → not enough workers; the fix is capacity, not code.
3. `reliability.retries_window` high → something transient is failing repeatedly; check the
   breaker states and the worker logs for the retry reason.

### A chaos fault was left behind

```bash
curl -X DELETE "$GATEWAY/v1/chaos" -H "X-API-Key: $ADMIN_KEY"
```

Faults expire on their own; the `ChaosFaultLeftBehind` alert fires if one lives longer than
15 minutes. Never leave `AEGISFLOW_ALLOW_CRASH=1` enabled in production.

## Routine operations

### Deploy

```bash
docker build -t ghcr.io/<owner>/aegisflow-backend:<tag> .
kubectl set image deploy/gateway gateway=ghcr.io/<owner>/aegisflow-backend:<tag> -n aegisflow
kubectl set image deploy/worker  worker=ghcr.io/<owner>/aegisflow-backend:<tag>  -n aegisflow
kubectl rollout status deploy/worker -n aegisflow
```

Workers get a 45 second termination grace period and drain in-flight jobs on `SIGTERM`, so
rolling deploys do not lose work. Roll back with `kubectl rollout undo`.

### Schema changes

Tables are created by `init_db()` at startup (idempotent `CREATE TABLE IF NOT EXISTS`). For a
destructive change, introduce the new column as nullable, deploy, backfill, then drop the old
one in a later release.

### Retraining the models

```bash
python ml/train.py --csv your_data.csv     # text,label columns
```

Artifacts land in `ml/artifacts`. The container build trains them, so a deploy always ships a
matching model. Workers report which artifacts loaded through `GET /v1/models`.

### Capacity planning

Measure, then decide:

```bash
python scripts/drills.py --rps 50 --duration 30
```

The report includes achieved rps, submit percentiles, end-to-end percentiles and drain time. If
`still_pending` is non-zero at the end of a run, the fleet could not keep up at that rate.

## Alerts and what to do

| Alert | First action |
| --- | --- |
| `GatewayDown` | check replicas and readiness; ingest is offline |
| `NoWorkersOnline` | scale workers; queued work is safe |
| `QueueBacklogGrowing` | scale workers, verify no chaos fault is active |
| `OutboxNotDraining` | broker connectivity, relay health |
| `LatencyRegression` | split compute vs queue latency in `/v1/stats` |
| `DeadLetterRate` | inspect `/v1/dlq`, fix the cause, replay |
| `CircuitBreakerOpen` | find the failing dependency; the breaker is protecting you |
