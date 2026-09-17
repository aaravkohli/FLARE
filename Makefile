# =============================================================================
# FLARE Makefile — Developer Task Automation
# =============================================================================

.PHONY: help setup start dev test test-frontend test-all lint build-frontend health stop clean docker-dev docker-prod

help:
	@echo "FLARE Commands:"
	@echo "  make setup          - Install Python & Node dependencies, build frontend"
	@echo "  make start          - Start full simulation stack (backend + frontend)"
	@echo "  make dev            - Alias for 'make start'"
	@echo "  make stop           - Stop all running FLARE local processes"
	@echo "  make health         - Run diagnostic health check on all services"
	@echo "  make test           - Run full Python test suite (pytest)"
	@echo "  make test-frontend  - Run frontend unit tests"
	@echo "  make test-all       - Run both Python and frontend test suites"
	@echo "  make lint           - Run frontend linter (oxlint)"
	@echo "  make build-frontend - Build frontend production bundle"
	@echo "  make docker-dev     - Launch simulation stack in Docker"
	@echo "  make docker-prod    - Launch production stack in Docker"
	@echo "  make clean          - Remove temporary caches and logs"

setup:
	@./scripts/setup.sh

start:
	@./scripts/start.sh

dev: start

stop:
	@./stop_local_simulation.sh

health:
	@./venv/bin/python scripts/healthcheck.py

test:
	@./venv/bin/pytest

test-frontend:
	@cd frontend-react && npm test

test-all: test test-frontend

lint:
	@cd frontend-react && npm run lint

build-frontend:
	@cd frontend-react && npm run build

docker-dev:
	@docker compose -f docker-compose.dev.yml up --build

docker-prod:
	@docker compose -f docker-compose.prod.yml up --build

clean:
	@find . -type f -name "*.pyc" -delete
	@find . -type d -name "__pycache__" -delete
	@find . -type f -name ".DS_Store" -delete
	@rm -rf .pytest_cache frontend-react/.vite
	@echo "Cleaned caches."
