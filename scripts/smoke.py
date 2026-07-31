"""End-to-end smoke test for a running AegisFlow stack.

Verifies the properties the platform claims, in order:

1. the gateway is ready and the model registry is loaded;
2. a valid request completes with a real prediction;
3. an invalid request is rejected at the edge (no queued garbage);
4. a batch drains completely;
5. an injected model fault produces retries, then a dead letter, and the
   dead letter can be replayed successfully once the fault clears;
6. the accounting invariant holds: no accepted job is unaccounted for.

    python scripts/smoke.py [--gateway http://127.0.0.1:8000]
"""

from __future__ import annotations

import argparse
import contextlib
import sys
import time

import httpx

# Windows consoles default to cp1252; keep the output readable everywhere.
with contextlib.suppress(AttributeError, OSError):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PASS = "PASS"
FAIL = "FAIL"
results: list[tuple[str, str, str]] = []


def record(name: str, ok: bool, detail: str = "") -> bool:
    results.append((PASS if ok else FAIL, name, detail))
    marker = "+" if ok else "x"
    print(f" {marker} {name}{f' — {detail}' if detail else ''}", flush=True)
    return ok


def wait_until(predicate, timeout_s: float = 30.0, interval_s: float = 0.5) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval_s)
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gateway", default="http://127.0.0.1:8000")
    parser.add_argument("--key", default="demo-key-aegisflow")
    parser.add_argument("--admin-key", default="admin-key-aegisflow")
    args = parser.parse_args()

    client = httpx.Client(base_url=args.gateway, timeout=30.0, headers={"X-API-Key": args.key})
    admin = {"X-API-Key": args.admin_key}

    print("\nAegisFlow smoke test")
    print(f"gateway: {args.gateway}\n")

    # 1 — readiness -------------------------------------------------------
    ready = client.get("/health/ready")
    record("gateway ready", ready.status_code == 200, f"status {ready.status_code}")
    models = client.get("/v1/models").json()["models"]
    loaded = [m["name"] for m in models if m["loaded"]]
    record("model registry", len(models) >= 3, f"{len(loaded)}/{len(models)} artifacts loaded")

    # 2 — happy path ------------------------------------------------------
    response = client.post(
        "/v1/predict",
        json={
            "model": "sentiment-v1",
            "input": {"text": "the courier arrived early and the fabric feels premium"},
            "wait_ms": 8000,
        },
    )
    job = response.json()["job"]
    record(
        "synchronous prediction",
        job["status"] == "succeeded" and bool((job.get("result") or {}).get("label")),
        f"{job['status']} → {(job.get('result') or {}).get('label')} in {job.get('total_ms')}ms",
    )

    # 3 — edge validation --------------------------------------------------
    invalid = client.post("/v1/predict", json={"model": "sentiment-v1", "input": {"text": ""}})
    record("empty payload rejected at the edge", invalid.status_code == 422, f"status {invalid.status_code}")
    unknown = client.post("/v1/predict", json={"model": "does-not-exist", "input": {"text": "hi"}})
    record("unknown model rejected", unknown.status_code == 422, f"status {unknown.status_code}")

    # 4 — batch drain ------------------------------------------------------
    batch = client.post(
        "/v1/predict/batch",
        json={"model": "sentiment-v2", "inputs": [{"text": f"order {i} arrived late and damaged"} for i in range(20)]},
    ).json()
    batch_ids = [row["id"] for row in batch["jobs"] if row.get("id")]

    def batch_done() -> bool:
        states = [client.get(f"/v1/jobs/{job_id}").json()["status"] for job_id in batch_ids]
        return all(state in ("succeeded", "failed", "dlq") for state in states)

    record("batch of 20 drained", wait_until(batch_done, timeout_s=60), f"{len(batch_ids)} jobs")

    # 5 — chaos: retries, dead letter, replay -------------------------------
    client.post(
        "/v1/chaos",
        headers=admin,
        json={"target": "model", "mode": "error", "probability": 1.0, "latency_ms": 0, "ttl_s": 20},
    )
    time.sleep(1.5)  # let every worker pick the fault up from the fault table
    poisoned = client.post(
        "/v1/predict", json={"model": "sentiment-v1", "input": {"text": "this one will fail on purpose"}}
    ).json()["job"]["id"]

    def dead_lettered() -> bool:
        return client.get(f"/v1/jobs/{poisoned}").json()["status"] == "dlq"

    parked = wait_until(dead_lettered, timeout_s=45)
    detail = client.get(f"/v1/jobs/{poisoned}").json()
    record("failing job retried then dead-lettered", parked, f"{detail['attempts']} attempts")

    client.request("DELETE", "/v1/chaos", headers=admin)
    dlq_rows = client.get("/v1/dlq").json()["dead_letters"]
    target = next((row for row in dlq_rows if row["job_id"] == poisoned), None)
    replayed = False
    if target:
        client.post(f"/v1/dlq/{target['id']}/replay", headers=admin)
        replayed = wait_until(
            lambda: client.get(f"/v1/jobs/{poisoned}").json()["status"] == "succeeded", timeout_s=45
        )
    record("dead letter replayed successfully", replayed, f"job {poisoned[:14]}")

    # 6 — accounting invariant ---------------------------------------------
    stats = client.get("/v1/stats").json()
    unaccounted = stats["reliability"]["unaccounted_jobs"]
    record("no unaccounted jobs", unaccounted == 0, f"unaccounted={unaccounted}")
    record(
        "workers reporting",
        len(stats["workers"]) >= 1,
        f"{len(stats['workers'])} online, queue depth {stats['queue']['depth']}",
    )

    failures = [row for row in results if row[0] == FAIL]
    print(f"\n{len(results) - len(failures)}/{len(results)} checks passed")
    if failures:
        for _, name, detail in failures:
            print(f"  FAILED: {name} ({detail})")
        return 1
    print("platform behaved as specified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
