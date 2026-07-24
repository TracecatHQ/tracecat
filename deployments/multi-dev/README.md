# Multi-dev shared infrastructure

This development-only profile runs one shared Postgres, Temporal dev server,
MinIO, and Redis stack on a VPS. Each Tracecat instance is one combined Python
process with its own Postgres database, Temporal namespace, MinIO bucket names,
Redis database index, and host ports.

The infrastructure compose project is named `tracecat-infra`. Instance project
names come from `TRACECAT_INSTANCE`. All published ports bind to localhost; use
SSH tunnels or a separately managed reverse proxy for remote access.

## Quickstart

Run these commands from the repository root.

1. Start the shared infrastructure. The checked-in defaults are development
   credentials; set matching `TRACECAT__POSTGRES_*` and `MINIO_ROOT_*` values in
   your shell before the first start if the VPS is not disposable.

   ```bash
   docker compose -f deployments/multi-dev/docker-compose.infra.yml up -d
   ```

2. Create and edit one environment file per instance. Assign a unique instance
   name, API/UI ports, Redis DB index, and secrets.

   ```bash
   cp deployments/multi-dev/instance.env.example deployments/multi-dev/alpha.env
   openssl rand -hex 32
   uv run python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
   ```

   Use the Fernet output for `TRACECAT__DB_ENCRYPTION_KEY` and separate random
   values for `TRACECAT__SERVICE_KEY`, `TRACECAT__SIGNING_SECRET`, and
   `USER_AUTH_SECRET`. Redis supports DB indexes 0 through 15 by default, so this
   profile supports up to 16 isolated indexes without changing Redis config.

3. Bootstrap the instance database and Temporal namespace once. The Temporal
   `1.8.0` image was verified to provide both `operator namespace create` and the
   `--address`/`--namespace` flags used here.

   ```bash
   set -a
   . deployments/multi-dev/alpha.env
   set +a

   docker compose -f deployments/multi-dev/docker-compose.infra.yml exec -T postgres \
     createdb -U "$TRACECAT__POSTGRES_USER" "$TRACECAT_INSTANCE"
   docker compose -f deployments/multi-dev/docker-compose.infra.yml exec -T temporal \
     temporal operator namespace create \
       --address localhost:7233 \
       --namespace "$TRACECAT_INSTANCE"
   ```

   Bootstrap is documented instead of modeled as an init service because the
   shared infrastructure and each instance have intentionally independent
   Compose lifecycles. Explicit one-time commands also make duplicate database
   names or namespaces visible instead of hiding them in restart behavior.

4. Start the headless instance. Add `--profile ui` to start its optional frontend.

   ```bash
   docker compose \
     --env-file deployments/multi-dev/alpha.env \
     -f deployments/multi-dev/docker-compose.instance.yml \
     up -d --build
   ```

   ```bash
   docker compose \
     --env-file deployments/multi-dev/alpha.env \
     -f deployments/multi-dev/docker-compose.instance.yml \
     --profile ui up -d --build
   ```

5. Stop an instance without deleting its cache volume, then stop shared infra
   after all instances are down. These commands preserve all named volumes.

   ```bash
   docker compose \
     --env-file deployments/multi-dev/alpha.env \
     -f deployments/multi-dev/docker-compose.instance.yml \
     down
   docker compose -f deployments/multi-dev/docker-compose.infra.yml down
   ```

## Memory and concurrency knobs

The instance compose file uses small development defaults:

| Variable | Default | Effect |
| --- | ---: | --- |
| `MALLOC_ARENA_MAX` | `2` | Limits glibc allocator arenas and retained heap growth. |
| `TEMPORAL__THREADPOOL_MAX_WORKERS` | `4` | Caps each applicable worker activity thread pool. |
| `TEMPORAL__MAX_CONCURRENT_ACTIVITIES` | `8` | Caps DSL worker activities in flight. |
| `TEMPORAL__MAX_CONCURRENT_WORKFLOW_TASKS` | `4` | Caps DSL workflow tasks; keep this at least `2`. |

Lower concurrency first when a small VPS is memory- or CPU-constrained. The
combined process still creates separate activity thread pools for worker types,
but it imports the Tracecat package only once.

LiteLLM remains external and optional. By default the instance reaches a proxy
on VPS host port 4000 through `host.docker.internal`; change
`TRACECAT__LITELLM_BASE_URL` when the proxy lives elsewhere. The frontend is
also optional and is excluded unless the `ui` profile is selected.
