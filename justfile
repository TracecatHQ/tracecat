# https://github.com/casey/just
set dotenv-load

default:
  @just --list
test:
	pytest --cache-clear tests/unit  tests/playbooks --temporal-no-restart --tracecat-no-restart -x
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
	cd frontend && pnpm lint && cd ..
lint-app:
	ruff check .

lint: lint-ui lint-app

gen-client:
	cd frontend && pnpm generate-client && cd ..
