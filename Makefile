.PHONY: setup up down logs infra-up infra-down backend-check frontend-check test verify

setup:
	@test -f .env || cp .env.example .env

up: setup
	./run.sh up

down:
	./run.sh down

logs:
	tail -f logs/*.log

infra-up:
	docker compose -f docker-compose.infra.yml -p involo up -d --wait

infra-down:
	docker compose -f docker-compose.infra.yml -p involo down

backend-check:
	cd backend && uv run ruff check .
	cd backend && uv run mypy app
	cd backend && uv run pytest

frontend-check:
	cd frontend && npm run lint
	cd frontend && npm run typecheck
	cd frontend && npm test -- --run
	cd frontend && npm run build

test: backend-check frontend-check

verify: test
	docker compose -f docker-compose.infra.yml -p involo config --quiet
