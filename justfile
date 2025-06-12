# https://github.com/casey/just
set dotenv-load

default:
  @just --list
test:
	pytest --cache-clear tests/registry tests/unit tests/playbooks -x
down:
	docker compose down --remove-orphans
clean:
	docker volume ls -q | xargs -r docker volume rm
clean-images:
	docker images "tracecat*" -q | xargs -r docker rmi
dev:
	docker compose -f docker-compose.dev.yml up
build-dev:
	docker compose -f docker-compose.dev.yml build --no-cache
up:
	docker compose up

build:
	docker compose build --no-cache

lint-ui:
	cd frontend && pnpm lint:fix && cd ..
lint-app:
	ruff check

lint-fix-ui:
	cd frontend && pnpm lint:fix && pnpm format:write && cd ..
lint-fix-app:
	ruff check . && ruff format .

lint: lint-ui lint-app
lint-fix: lint-fix-ui lint-fix-app

mypy path:
	mypy --ignore-missing-imports {{path}}
gen-client:
	cd frontend && pnpm generate-client && cd ..
	just lint-fix
# Update version number. If no version is provided, increments patch version.
update-version *after='':
	@-./scripts/update-version.sh {{after}}

# CLI shortcuts
# Check that cli is installed
_check-cli:
	#!/usr/bin/env sh
	set -e
	command -v tracecat >/dev/null 2>&1 || { echo "Error: Tracecat CLI is not installed" >&2; exit 1; }

gen-api: _check-cli
	LOG_LEVEL=ERROR tracecat dev generate-spec --update-docs

gen-integrations: _check-cli
	tracecat dev gen-integrations

gen-functions: _check-cli
	tracecat dev gen-functions

# Check temporal CLI is installed
_check-temporal-cli:
	#!/usr/bin/env sh
	set -e
	command -v temporal >/dev/null 2>&1 || { echo "Error: Temporal CLI is not installed" >&2; exit 1; }

# Stop all running Temporal workflow executions
temporal-stop-all: _check-temporal-cli
	temporal workflow terminate --query "ExecutionStatus='Running'" --namespace default --yes
