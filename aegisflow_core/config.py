"""Central configuration for every AegisFlow service.

All services share one image and one settings object; behaviour is switched with
environment variables so the same code runs on a laptop (SQLite + DB broker),
on docker-compose (Postgres + Redpanda) and on Kubernetes.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BrokerKind = Literal["db", "redis", "kafka"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
        protected_namespaces=(),
    )

    # ---- identity -------------------------------------------------------
    service_name: str = "gateway"
    app_env: str = "local"
    version: str = "1.0.0"
    instance_id: str = Field(default_factory=lambda: os.environ.get("HOSTNAME", "local"))

    # ---- storage --------------------------------------------------------
    database_url: str = "sqlite+aiosqlite:///./data/aegisflow.db"
    db_pool_size: int = 10
    db_statement_timeout_ms: int = 8000

    # ---- broker ---------------------------------------------------------
    broker: BrokerKind = "db"
    redis_enabled: bool = False          # cache + distributed rate limiting
    redis_url: str = "redis://localhost:6379/0"
    kafka_bootstrap: str = "localhost:19092"
    topic_requests: str = "inference.requests"
    topic_retry: str = "inference.retry"
    topic_dlq: str = "inference.dlq"
    consumer_group: str = "inference-workers"
    visibility_timeout_s: int = 45

    # ---- gateway --------------------------------------------------------
    api_keys: str = "demo-key-aegisflow"
    admin_api_key: str = "admin-key-aegisflow"
    require_api_key: bool = True
    rate_limit_per_minute: int = 12000
    rate_limit_burst: int = 800
    cors_origins: str = "*"
    sync_wait_ms_max: int = 8000
    lab_url: str = "http://localhost:8100"
    gateway_url: str = "http://localhost:8000"

    # ---- worker ---------------------------------------------------------
    worker_concurrency: int = 4
    worker_prefetch: int = 16
    # Writing an explicit "running" transition costs one extra DB round trip per
    # job. Off by default; turn it on when you want per-job state transitions.
    track_running_state: bool = False
    max_attempts: int = 4
    retry_base_ms: int = 250
    retry_max_ms: int = 8000
    job_timeout_ms: int = 5000
    heartbeat_interval_s: int = 5
    artifacts_dir: str = "ml/artifacts"

    # ---- resilience -----------------------------------------------------
    breaker_failure_threshold: int = 5
    breaker_reset_timeout_s: float = 10.0
    cache_ttl_s: int = 300
    metrics_port: int = 9101

    # ---- misc -----------------------------------------------------------
    log_level: str = "INFO"
    log_json: bool = True
    retention_hours: int = 72

    @field_validator("database_url")
    @classmethod
    def _normalise_db_url(cls, value: str) -> str:
        # Accept the URLs handed out by Neon/Render/Fly and make them async.
        if value.startswith("postgres://"):
            value = value.replace("postgres://", "postgresql+asyncpg://", 1)
        elif value.startswith("postgresql://"):
            value = value.replace("postgresql://", "postgresql+asyncpg://", 1)
        if value.startswith("sqlite://") and "+aiosqlite" not in value:
            value = value.replace("sqlite://", "sqlite+aiosqlite://", 1)
        # asyncpg does not understand libpq query args.
        if "asyncpg" in value and "?" in value:
            base, _, query = value.partition("?")
            keep = [p for p in query.split("&") if not p.startswith(("sslmode", "channel_binding", "options"))]
            value = base + ("?" + "&".join(keep) if keep else "")
        return value

    @property
    def api_key_set(self) -> set[str]:
        return {k.strip() for k in self.api_keys.split(",") if k.strip()}

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
