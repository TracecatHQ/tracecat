# https://github.com/casey/just
set dotenv-load

default:
  @just --list
test:
	pytest --cache-clear tests/registry tests/unit tests/playbooks -x

# Run fast unit tests in parallel (excludes slow/integration/temporal tests)
test-fast:
	uv run pytest tests/unit -m "not (slow or integration or temporal)" -n auto -x

# Run only workflow/temporal tests
test-temporal:
	uv run pytest tests/temporal -x

# Run specific test file with parallel execution
test-file file:
	uv run pytest {{file}} -n auto -x

# Run tests matching a keyword
test-k keyword:
	uv run pytest tests/unit -k "{{keyword}}" -n auto -x

# Run backend benchmarks inside Docker (required for nsjail on macOS)
bench *args:
	docker run --rm \
		--network tracecat_default \
		--cap-add SYS_ADMIN \
		--security-opt seccomp=unconfined \
		--env-file .env \
		-e REDIS_URL=redis://redis:6379 \
		-e TRACECAT__BLOB_STORAGE_ENDPOINT=http://minio:9000 \
		-e TRACECAT__DB_URI=postgresql+psycopg://postgres:postgres@postgres_db:5432/postgres \
		-v "$(pwd)/tests:/app/tests:ro" \
		--entrypoint sh \
		tracecat-executor \
		-c "pip install pytest pytest-anyio anyio -q && python -m pytest tests/backends/test_backend_benchmarks.py -v -s {{args}}"

down:
	docker compose down --remove-orphans
clean:
	docker volume ls -q | xargs -r docker volume rm
clean-images:
	docker images --filter "reference=tracecat*" | awk 'NR>1 && $1 != "<none>" && $2 != "<none>" {print $1 ":" $2}' | xargs -r -n 1 docker rmi
clean-dangling:
	docker image prune -f
dev:
	docker compose -f docker-compose.dev.yml up
dev-ui:
	npx @agentdeskai/browser-tools-server@1.2.0
build-dev *services:
	docker compose -f docker-compose.dev.yml build --no-cache {{services}}
local:
	NODE_ENV=production NEXT_PUBLIC_APP_ENV=production TRACECAT__APP_ENV=production docker compose -f docker-compose.local.yml up
build-local *services:
	docker compose -f docker-compose.local.yml build {{services}}
rebuild-local *services:
	docker compose -f docker-compose.local.yml build --no-cache {{services}}
up:
	docker compose up

build:
	docker compose build --no-cache

lint-ui:
	pnpm -C frontend lint:fix
lint-app:
	uv run ruff check

lint-fix-ui:
	pnpm -C frontend check
lint-fix-app:
	uv run ruff check . --fix && uv run ruff format .

lint: lint-ui lint-app
lint-fix: lint-fix-ui lint-fix-app
fix: lint-fix

mypy path:
	mypy --ignore-missing-imports {{path}}
gen-client:
	pnpm -C frontend generate-client
	just lint-fix
gen-client-ci:
	pnpm -C frontend generate-client-ci
	just lint-fix
# Update version number. If no version is provided, increments patch version.
update-version *after='':
	@-./scripts/update-version.sh {{after}}

# Check temporal CLI is installed
_check-temporal-cli:
	#!/usr/bin/env sh
	set -e
	command -v temporal >/dev/null 2>&1 || { echo "Error: Temporal CLI is not installed" >&2; exit 1; }

# Stop all running Temporal workflow executions
temporal-stop-all: _check-temporal-cli
	temporal workflow terminate --query "ExecutionStatus='Running'" --namespace default --yes

# Manage multiple Tracecat clusters (run `just cluster` for usage)
cluster *args:
	./scripts/cluster {{args}}
