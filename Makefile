SHELL := /bin/bash

PYTHON ?= python
PIP ?= $(PYTHON) -m pip
NPM ?= npm
COMPOSE ?= docker compose

BACKEND_DIR := backend
FRONTEND_DIR := frontend

.PHONY: help config install install-backend install-backend-dev install-frontend backend frontend dev shell \
	docker-start docker-up docker-stop docker-down docker-status \
	check-docker-mounts test test-backend test-backend-cov test-frontend typecheck build-frontend \
	contracts docs docs-serve clean release-readiness release-readiness-full check

help:
	@echo "Anvil developer commands"
	@echo ""
	@echo "Setup:"
	@echo "  make config              Create .env and config.yaml from examples when missing"
	@echo "  make install             Install backend and frontend dependencies"
	@echo "  make install-backend-dev Install backend with test/docs extras"
	@echo ""
	@echo "Run:"
	@echo "  make backend             Start FastAPI gateway on 127.0.0.1:18000"
	@echo "  make frontend            Start Next.js frontend"
	@echo "  make dev                 Start backend + frontend locally"
	@echo "  make docker-start        Build and start Docker Compose"
	@echo ""
	@echo "Verify:"
	@echo "  make test                Run backend and frontend tests"
	@echo "  make test-backend-cov    Run backend tests with coverage.xml"
	@echo "  make check-docker-mounts Check Docker compose mount safety"
	@echo "  make typecheck           Run frontend typecheck"
	@echo "  make contracts           Regenerate backend/frontend contracts"
	@echo "  make release-readiness   Run quick release readiness gates"
	@echo "  make docs                Build the MkDocs site"

config:
	@test -f .env || cp .env.example .env
	@test -f config.yaml || cp config.example.yaml config.yaml
	@echo "Config ready. Edit .env for secrets and config.yaml for model routing."

install: install-backend install-frontend

install-backend:
	cd $(BACKEND_DIR) && $(PIP) install -e ".[observability]"

install-backend-dev:
	cd $(BACKEND_DIR) && $(PIP) install -e ".[observability,test,docs]"

install-frontend:
	cd $(FRONTEND_DIR) && $(NPM) ci

backend:
	./scripts/start-backend.sh

frontend:
	./scripts/start-frontend.sh

dev:
	./scripts/start-fullstack.sh

shell:
	./scripts/start-shell.sh

docker-start docker-up:
	./scripts/start-docker.sh

docker-stop docker-down:
	./scripts/stop-docker.sh

docker-status:
	./scripts/status-docker.sh

check-docker-mounts:
	$(PYTHON) scripts/check-docker-mount-safety.py

test: test-backend test-frontend

test-backend:
	cd $(BACKEND_DIR) && $(PYTHON) -m pytest -q

test-backend-cov:
	cd $(BACKEND_DIR) && $(PYTHON) -m pytest -q --cov=app --cov=packages/harness/anvil --cov-report=term-missing --cov-report=xml

test-frontend:
	cd $(FRONTEND_DIR) && $(NPM) test

typecheck:
	cd $(FRONTEND_DIR) && $(NPM) run typecheck

build-frontend:
	cd $(FRONTEND_DIR) && $(NPM) run build

contracts:
	$(PYTHON) scripts/generate-contracts.py

docs:
	$(PYTHON) -m mkdocs build

docs-serve:
	$(PYTHON) -m mkdocs serve

clean:
	$(PYTHON) scripts/clean-dev-artifacts.py

release-readiness:
	$(PYTHON) scripts/run-release-readiness.py --profile quick

release-readiness-full:
	$(PYTHON) scripts/run-release-readiness.py --profile full

check: contracts check-docker-mounts test-backend test-frontend typecheck docs
