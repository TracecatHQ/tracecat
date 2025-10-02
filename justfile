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
	ruff check

lint-fix-ui:
	pnpm -C frontend check
lint-fix-app:
	ruff check . && ruff format .

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
