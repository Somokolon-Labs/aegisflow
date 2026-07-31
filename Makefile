# AegisFlow - developer entrypoints (Linux/macOS; use scripts/dev.ps1 on Windows)
PY ?= python3
VENV ?= .venv
BIN := $(VENV)/bin
GATEWAY ?= http://127.0.0.1:8000
LAB ?= http://127.0.0.1:8100

.PHONY: help setup train gateway worker relay lab up down smoke drill compose-up compose-down k8s-apply image lint clean

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "};{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

setup: ## create venv, install deps, train models, seed .env
	$(PY) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -r requirements.txt
	$(BIN)/python ml/train.py
	@test -f .env || cp .env.example .env

train: ## retrain the bundled models
	$(BIN)/python ml/train.py

gateway: ## run the public API
	$(BIN)/uvicorn services.gateway.main:app --host 0.0.0.0 --port 8000 --reload

worker: ## run one inference worker
	METRICS_PORT=9101 $(BIN)/python -m services.worker.main

relay: ## run the outbox relay + reaper
	METRICS_PORT=9102 $(BIN)/python -m services.relay.main

lab: ## run the resilience lab
	$(BIN)/uvicorn services.lab.main:app --host 0.0.0.0 --port 8100

up: ## background: gateway + 2 workers + relay + lab
	@mkdir -p data
	@$(BIN)/uvicorn services.gateway.main:app --host 0.0.0.0 --port 8000 > data/gateway.log 2>&1 & echo $$! > data/gateway.pid
	@WORKER_ID=worker-a METRICS_PORT=9101 $(BIN)/python -m services.worker.main > data/worker-a.log 2>&1 & echo $$! > data/worker-a.pid
	@WORKER_ID=worker-b METRICS_PORT=9103 $(BIN)/python -m services.worker.main > data/worker-b.log 2>&1 & echo $$! > data/worker-b.pid
	@METRICS_PORT=9102 $(BIN)/python -m services.relay.main > data/relay.log 2>&1 & echo $$! > data/relay.pid
	@$(BIN)/uvicorn services.lab.main:app --host 0.0.0.0 --port 8100 > data/lab.log 2>&1 & echo $$! > data/lab.pid
	@sleep 5 && echo "gateway $(GATEWAY)/docs | lab $(LAB)/docs"

down: ## stop background services
	@for f in data/*.pid; do [ -f $$f ] && kill `cat $$f` 2>/dev/null || true; rm -f $$f; done; echo stopped

smoke: ## one inference request end to end
	@curl -s -X POST $(GATEWAY)/v1/predict -H 'content-type: application/json' -H 'X-API-Key: demo-key-aegisflow' \
	  -d '{"model":"sentiment-v1","input":{"text":"the courier arrived early and the fabric feels premium"},"wait_ms":5000}' | $(PY) -m json.tool

drill: ## chaos drill: SCENARIO=worker-loss RPS=40 DURATION=30
	@curl -s -X POST $(LAB)/v1/loadtest -H 'content-type: application/json' \
	  -d '{"scenario":"$(or $(SCENARIO),worker-loss)","rps":$(or $(RPS),40),"duration_s":$(or $(DURATION),30),"fault_at_s":8,"fault_duration_s":10,"concurrency":48}' | $(PY) -m json.tool

report: ## best numbers per scenario
	@curl -s $(LAB)/v1/report | $(PY) -m json.tool

compose-up: ## full stack in Docker (Postgres + Redpanda + Redis + Prometheus + Grafana + console)
	docker compose up -d --build

compose-down:
	docker compose down -v

image: ## build the backend image
	docker build -t aegisflow/backend:1.0.0 .

k8s-apply: ## deploy to the current kube context
	kubectl apply -k deploy/k8s

lint: ## ruff, if installed
	$(BIN)/python -m ruff check aegisflow_core services ml || true

clean:
	rm -rf data __pycache__ */__pycache__ .ruff_cache
