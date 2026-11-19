# OpsPilot - common commands. Run `make help` to see them all.
SHELL := /bin/bash
.DEFAULT_GOAL := help

OLLAMA_MODEL ?= qwen2.5:7b
KIND_CLUSTER ?= opspilot

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------- setup
.PHONY: setup
setup: ## Install Python deps (incl. dev tools) and create .env
	uv sync --all-groups
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example")
	uv run pre-commit install || true

.PHONY: models
models: ## Pull the local LLM into Ollama
	ollama pull $(OLLAMA_MODEL)

# ---------------------------------------------------------------- Phase 1: local stack
.PHONY: up
up: ## Build and start the whole stack in Docker
	docker compose up -d --build
	@echo ""
	@echo "  UI          http://localhost:8501"
	@echo "  API docs    http://localhost:8000/docs"
	@echo "  Grafana     http://localhost:3000   (admin / admin)"
	@echo "  Prometheus  http://localhost:9090"
	@echo "  MLflow      http://localhost:5001"
	@echo "  Qdrant      http://localhost:6333/dashboard"

.PHONY: down
down: ## Stop the stack (data volumes are kept)
	docker compose down

.PHONY: nuke
nuke: ## Stop the stack AND delete all data volumes
	docker compose down -v

.PHONY: logs
logs: ## Follow API logs
	docker compose logs -f api

.PHONY: ps
ps: ## Show container status
	docker compose ps

.PHONY: dev-api
dev-api: ## Run the API on the host with auto-reload (needs `docker compose up -d qdrant`)
	uv run uvicorn opspilot.api.main:app --reload --port 8000

.PHONY: dev-ui
dev-ui: ## Run the Streamlit UI on the host
	cd ui && uv run --group ui streamlit run app.py

.PHONY: ingest
ingest: ## Re-index the knowledge base (local Qdrant)
	uv run python scripts/ingest.py --recreate

.PHONY: ask
ask: ## Ask a question from the terminal: make ask Q="redis is out of memory"
	@curl -s -X POST localhost:8000/chat -H 'content-type: application/json' \
	  -d "{\"question\": \"$(Q)\"}" | python3 -m json.tool

# ---------------------------------------------------------------- quality
.PHONY: lint
lint: ## Lint and check formatting
	uv run ruff check .
	uv run ruff format --check .

.PHONY: format
format: ## Auto-format the code
	uv run ruff check --fix .
	uv run ruff format .

.PHONY: test
test: ## Run unit tests (no Docker or LLM needed)
	uv run pytest

# ---------------------------------------------------------------- Phase 2: MLOps
.PHONY: eval
eval: ## Run the full evaluation and log it to MLflow
	uv run python -m evaluation.run_eval --run-name "$(OLLAMA_MODEL)-$$(date +%H%M)"

.PHONY: eval-quick
eval-quick: ## Evaluate on the first 5 questions only
	uv run python -m evaluation.run_eval --limit 5 --no-mlflow

.PHONY: eval-no-rerank
eval-no-rerank: ## Same evaluation without the reranker, to compare in MLflow
	RERANK_ENABLED=false uv run python -m evaluation.run_eval --run-name no-rerank

.PHONY: load-test
load-test: ## Start Locust (open http://localhost:8089)
	uv run --group load locust -f loadtest/locustfile.py --host http://localhost:8000

# ---------------------------------------------------------------- Phase 3: cloud
.PHONY: ingest-cloud
ingest-cloud: ## Load the knowledge base into Qdrant Cloud (needs cloud vars in .env)
	OPSPILOT_MODE=cloud uv run python scripts/ingest.py --recreate

.PHONY: eval-cloud
eval-cloud: ## Run the eval against Groq (same thresholds as the CI gate)
	LLM_PROVIDER=groq uv run python -m evaluation.run_eval --subset ci --sleep 4 \
	  --min-hit-rate 0.75 --min-faithfulness 0.7 --min-behaviour 1.0 --run-name groq-gate

.PHONY: ui-direct
ui-direct: ## Run the UI exactly like Streamlit Cloud does (in-process, cloud mode)
	cd ui && OPSPILOT_MODE=cloud UI_BACKEND=direct uv run --group ui streamlit run app.py

# ---------------------------------------------------------------- Phase 4: Kubernetes
.PHONY: k8s-create
k8s-create: ## Create a local kind cluster
	kind create cluster --name $(KIND_CLUSTER) --config k8s/kind-config.yaml

.PHONY: k8s-load
k8s-load: ## Build images and load them into kind
	docker build -f docker/api.Dockerfile -t opspilot-api:local .
	docker build -f docker/ui.Dockerfile -t opspilot-ui:local .
	kind load docker-image opspilot-api:local opspilot-ui:local --name $(KIND_CLUSTER)

.PHONY: k8s-deploy
k8s-deploy: ## Install/upgrade the Helm chart
	helm upgrade --install opspilot k8s/helm/opspilot -n opspilot --create-namespace --wait --timeout 10m

.PHONY: k8s-forward
k8s-forward: ## Port-forward the UI (8501) and API (8000) from the cluster
	@echo "UI on http://localhost:8501, API on http://localhost:8000 (Ctrl+C to stop)"
	@trap 'kill 0' INT; \
	kubectl -n opspilot port-forward svc/opspilot-ui 8501:8501 & \
	kubectl -n opspilot port-forward svc/opspilot-api 8000:8000 & \
	wait

.PHONY: k8s-delete
k8s-delete: ## Delete the kind cluster
	kind delete cluster --name $(KIND_CLUSTER)

# ---------------------------------------------------------------- misc
.PHONY: clean
clean: ## Remove caches and reports
	rm -rf .pytest_cache .ruff_cache evaluation/reports
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
