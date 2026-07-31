# One image, four entrypoints (gateway, worker, relay, lab).
# Models are trained during the build so no binaries live in git.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY aegisflow_core ./aegisflow_core
COPY services ./services
COPY ml ./ml
# Shipped so `docker compose exec gateway python scripts/smoke.py` works.
COPY scripts ./scripts

# Train the bundled models at build time (deterministic, ~5 seconds).
RUN python ml/train.py --per-class 700 --seed 17 \
 && find /app -name "__pycache__" -type d -prune -exec rm -rf {} +

RUN useradd --create-home --uid 10001 aegis \
 && mkdir -p /app/data \
 && chown -R aegis:aegis /app
USER aegis

ENV DATABASE_URL=sqlite+aiosqlite:////app/data/aegisflow.db \
    ARTIFACTS_DIR=/app/ml/artifacts \
    PORT=8000

EXPOSE 8000 8100 9101

HEALTHCHECK --interval=20s --timeout=4s --start-period=20s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${HEALTHCHECK_PORT:-8000}/health" || exit 1

# Default: the public gateway. Override `command` for the other services.
CMD ["sh", "-c", "uvicorn services.gateway.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips=* --workers ${WEB_CONCURRENCY:-1}"]
