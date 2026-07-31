# Fault tolerance

Each mechanism below exists because a specific failure would otherwise lose or duplicate
work. Every one is exercised by a drill in the resilience lab, so a regression shows up as a
failed verdict rather than a surprise in production.

## 1. Transactional outbox

**Failure it prevents:** acknowledging a request that never gets queued (publish-then-commit),
or queueing work that was never persisted (commit-then-publish).

The job row, its audit event and the publish intent are written in one transaction. A separate
relay publishes unsent rows and stamps `published_at`. Duplicate publishes are harmless because
the consumer deduplicates.

```
BEGIN
  INSERT INTO jobs            (...)
  INSERT INTO job_events      (submitted)
  INSERT INTO outbox          (topic, key, payload)
COMMIT            -- only now does the client see 202
```

## 2. Leases instead of ownership

**Failure it prevents:** work vanishing with a crashed consumer.

A claim sets `status='inflight'`, a `consumer` token and `lease_until = now + visibility_timeout`.
If the worker dies, the relay's reaper flips the row back to `pending` once the lease expires and
another worker picks it up. `attempt` increments on each delivery, so a message cannot loop
forever.

## 3. Ack inside the result transaction

**Failure it prevents:** double-processing after a crash between "wrote result" and "acked".

With the DB broker the queue and the result live in the same database, so both are committed
together — exactly-once without a distributed transaction. On Kafka and Redis the ack is a
separate operation, so the inbox table (`processed_messages`) makes a replay a no-op: seen key,
ack and move on.

## 4. Permanent vs transient errors

**Failure it prevents:** a malformed payload consuming four attempts and then poisoning the DLQ,
or a transient blip failing a valid job.

`PermanentError` (unknown model, empty text, oversized payload) fails immediately with no retry.
Everything else is transient: exponential backoff with full jitter, capped at `MAX_ATTEMPTS`,
then dead-letter. Obvious cases are rejected at the edge with 422 so they never become jobs.

## 5. Circuit breaker

**Failure it prevents:** hammering a dependency that is already down.

Three states with a single trial call in half-open. The gateway's publish path and the relay's
publish path each own a breaker; state is exported as
`aegisflow_circuit_breaker_state` (0 closed, 1 half-open, 2 open).

## 6. Bulkhead and timeouts

**Failure it prevents:** one slow model saturating the process and starving healthy work.

Worker concurrency is a semaphore sized by `WORKER_CONCURRENCY`; each job runs under
`JOB_TIMEOUT_MS`. Timeouts are transient, so they retry.

## 7. Graceful degradation

**Failure it prevents:** total outage because one non-critical dependency is unavailable.

- Missing model artifacts → deterministic fallback, result flagged `degraded`.
- Redis down → cache and rate limiter fall back to in-process behaviour (fail open).
- Broker down → ingest continues, backlog accumulates in the outbox.
- Database down → the platform stops accepting. This is the one hard dependency, and it is
  deliberate: without it there is no durability to promise.

## 8. Graceful shutdown

`SIGTERM` stops polling, drains in-flight jobs for up to 20 seconds, then exits. Kubernetes
gives workers a 45 second grace period, so a rolling deploy does not orphan work.

## Drills

| Scenario | Injected fault | Property under test |
| --- | --- | --- |
| `baseline` | none | reference throughput and latency |
| `worker-loss` | `worker/pause` | lease expiry, requeue, catch-up, zero loss |
| `broker-outage` | `broker/error` | outbox durability, ingest availability during outage |
| `db-slowdown` | `db/latency 800ms` | timeouts, retries, breaker behaviour under slow storage |
| `poison-payloads` | `model/error 30%` | retry budget, dead-lettering, queue keeps moving |
| `burst` | none (3x rate) | backpressure and elasticity |

Run them all and write a report:

```bash
python scripts/drills.py --rps 25 --duration 24     # writes docs/benchmarks.md
```

Or drive a single fault by hand:

```bash
curl -X POST $GATEWAY/v1/chaos -H 'X-API-Key: admin-key-aegisflow' \
  -H 'content-type: application/json' \
  -d '{"target":"worker","mode":"pause","probability":1,"ttl_s":20}'
```

Faults live in the database with a TTL, so they reach every replica within about half a second,
survive restarts and clear themselves. `crash` mode requires `AEGISFLOW_ALLOW_CRASH=1` so a
drill cannot hard-kill a process unless the environment opted in.

## What is not covered

- Multi-region failover and database HA are deployment concerns, not application ones. The
  application assumes one writable primary.
- Exactly-once *delivery* is impossible across a network boundary; what is delivered is
  exactly-once *effect*, via dedupe and idempotent writes.
- The relay is a single logical owner. Running two is safe (every write is idempotent) but
  wasteful.
