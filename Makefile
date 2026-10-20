# PatchLens — developer entry points.
#
# Everything runs without Docker by default: SQLite, the deterministic mock LLM
# and hashing embeddings. Docker Compose adds PostgreSQL + pgvector and Redis.
# Python lives in .venv at the repository root (created by `make setup`).

SHELL := /bin/bash
PY := .venv/bin/python
PIP := .venv/bin/pip
BACKEND := backend
VENV := .venv
PORT ?= 8000

.PHONY: help setup lock env-example fmt lint typecheck test test-cov test-fast ci \
        run worker migrate migration init-db demo-repo demo-review eval-seed eval-run clean \
        compose-up compose-down frontend-install frontend-dev frontend-lint frontend-build

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

## ------------------------------------------------------------------ environment
setup: ## Create the virtualenv and install locked dependencies
	python3.11 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r $(BACKEND)/requirements-dev.lock.txt
	@echo "Done. Next: make init-db && make demo-repo"

lock: ## Re-resolve both lockfiles from the .in files
	$(PIP) install --upgrade pip pip-tools
	cd $(BACKEND) && ../$(PY) -m piptools compile --quiet --strip-extras requirements.in \
		--output-file requirements.lock.txt
	cd $(BACKEND) && ../$(PY) -m piptools compile --quiet --strip-extras requirements-dev.in \
		--output-file requirements-dev.lock.txt

env-example: ## Regenerate .env.example from the settings model
	cd $(BACKEND) && ../$(PY) -m patchlens.cli config --write-env-example > ../.env.example

## -------------------------------------------------------------------- quality
fmt: ## Format Python sources
	cd $(BACKEND) && ../$(PY) -m ruff format patchlens tests migrations
	cd $(BACKEND) && ../$(PY) -m ruff check --fix patchlens tests migrations

lint: ## Lint without modifying files
	cd $(BACKEND) && ../$(PY) -m ruff format --check patchlens tests migrations
	cd $(BACKEND) && ../$(PY) -m ruff check patchlens tests migrations

typecheck: ## Static type checking
	cd $(BACKEND) && ../$(PY) -m mypy patchlens

test: ## Run the test suite
	cd $(BACKEND) && ../$(PY) -m pytest tests -q

test-cov: ## Run tests with a coverage report
	cd $(BACKEND) && ../$(PY) -m pytest tests -q --cov=patchlens --cov-report=term-missing

test-fast: ## Run only tests that need no database writes
	cd $(BACKEND) && ../$(PY) -m pytest tests/unit -q

ci: lint typecheck test ## What CI runs

## ------------------------------------------------------------------- database
init-db: ## Create tables in a development database (SQLite by default)
	cd $(BACKEND) && ../$(PY) -m patchlens.cli init-db

migrate: ## Apply migrations (production path)
	cd $(BACKEND) && ../$(PY) -m alembic upgrade head

migration: ## Autogenerate a migration: make migration m="add table"
	cd $(BACKEND) && ../$(PY) -m alembic revision --autogenerate -m "$(m)"

## ----------------------------------------------------------------------- run
run: ## Start the API on PORT (default 8000)
	cd $(BACKEND) && ../$(PY) -m patchlens.cli serve --port $(PORT)

worker: ## Start the background worker
	cd $(BACKEND) && ../$(PY) -m patchlens.cli worker

demo-repo: ## Register and index the bundled demo repository (no GitHub credentials needed)
	cd $(BACKEND) && repo_id=$$(../$(PY) -m patchlens.cli --json repo-add --owner demo --name patchlens-demo \
		--local-path "$(CURDIR)/examples/demo-repo" | ../$(PY) -c "import json,sys; print(json.load(sys.stdin)['id'])") \
		&& ../$(PY) -m patchlens.cli index --repository-id $$repo_id --commit-sha demo-local \
		&& echo $$repo_id > "$(CURDIR)/.demo-repository-id"

demo-review: ## Review the demo pull request against the indexed demo repository
	cd $(BACKEND) && ../$(PY) -m patchlens.cli review \
		--repository-id $$(cat "$(CURDIR)/.demo-repository-id") \
		--diff-file "$(CURDIR)/examples/demo-repo/pr.diff" --commit-sha demo-pr --include-rejected

eval-seed: ## Load the bundled evaluation datasets into the database
	cd $(BACKEND) && ../$(PY) -m patchlens.cli eval-seed --path eval/datasets

eval-run: ## Run the bundled retrieval and review evaluations
	cd $(BACKEND) && ../$(PY) -m patchlens.cli eval-run --dataset retrieval-demo-v1
	cd $(BACKEND) && ../$(PY) -m patchlens.cli eval-run --dataset review-demo-v1

## ------------------------------------------------------------------- docker
compose-up: ## PostgreSQL + pgvector and Redis for the production-shaped path
	docker compose up -d

compose-down: ## Stop the Compose services
	docker compose down

## ------------------------------------------------------------------ frontend
frontend-install: ## Install frontend dependencies
	cd frontend && npm install

frontend-dev: ## Run the Next.js dev server
	cd frontend && npm run dev

frontend-lint: ## Type-check and lint the frontend
	cd frontend && npm run lint && npm run typecheck

frontend-build: ## Production build of the frontend
	cd frontend && npm run build

clean: ## Remove caches and local artefacts
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache .mypy_cache backend/var
