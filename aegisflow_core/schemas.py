"""Public API contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PredictRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=(), json_schema_extra={
        "example": {"model": "sentiment-v1", "input": {"text": "the delivery was fast and the fabric feels great"}}
    })

    model: str = Field(default="sentiment-v1", description="Registered model name")
    input: dict[str, Any] = Field(default_factory=dict, description="Model input, e.g. {'text': '...'}")
    priority: int = Field(default=5, ge=1, le=9, description="1 = highest")
    idempotency_key: str | None = Field(default=None, max_length=128)
    wait_ms: int = Field(default=0, ge=0, le=15000, description="Block for the result up to N ms")
    tenant: str = Field(default="public", max_length=64)


class BatchPredictRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    model: str = "sentiment-v1"
    inputs: list[dict[str, Any]] = Field(default_factory=list, max_length=500)
    priority: int = Field(default=6, ge=1, le=9)
    tenant: str = "public"


class JobView(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    id: str
    status: Literal["queued", "running", "succeeded", "failed", "dlq"]
    model: str
    model_version: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str | None = None
    attempts: int = 0
    degraded: bool = False
    priority: int = 5
    queue_ms: float | None = None
    compute_ms: float | None = None
    total_ms: float | None = None
    worker_id: str | None = None
    trace_id: str | None = None
    tenant: str = "public"
    created_at: str | None = None
    finished_at: str | None = None


class SubmitResponse(BaseModel):
    job: JobView
    deduplicated: bool = False
    queue_position: int | None = None


class ChaosRequest(BaseModel):
    target: Literal["gateway", "worker", "model", "db", "broker"]
    mode: Literal["latency", "error", "drop", "pause", "crash"]
    probability: float = Field(default=1.0, ge=0.0, le=1.0)
    latency_ms: int = Field(default=400, ge=0, le=30000)
    ttl_s: int = Field(default=45, ge=1, le=1800)
    note: str | None = Field(default=None, max_length=200)


class LoadTestRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    scenario: Literal[
        "baseline", "worker-loss", "broker-outage", "db-slowdown", "poison-payloads", "burst"
    ] = "baseline"
    rps: int = Field(default=40, ge=1, le=2000)
    duration_s: int = Field(default=30, ge=3, le=600)
    model: str = "sentiment-v1"
    concurrency: int = Field(default=32, ge=1, le=512)
    fault_at_s: int = Field(default=8, ge=0, le=600)
    fault_duration_s: int = Field(default=10, ge=1, le=600)


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    checks: dict[str, Any] = Field(default_factory=dict)
