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

## Zygote mode (N instances, one container)

Zygote mode is the lower-memory alternative when several headless development
instances can share one container lifecycle. The parent imports the standalone
runtime once, freezes the imported Python object graph, and forks one child per
instance. Linux copy-on-write lets those children physically share most clean
import pages instead of paying for the same roughly 400 MB module graph in
every container.

Prefer the per-instance compose file when instances need independent container
restarts, health state, resource limits, or deployment lifecycles. In zygote
mode, restarting or replacing the one container restarts every child, and v1
does not restart a child that exits.

The zygote reads every `*.env` file from `deployments/multi-dev/instances/`.
Files contain only per-instance values; shared endpoints and concurrency
defaults remain in `docker-compose.zygote.yml`. Ports must be unique and fall
within the published `8100-8119` range.

1. Start the shared infrastructure as described above, then create instance
   manifests from the examples and replace every placeholder secret.

   ```bash
   cp deployments/multi-dev/instances/alpha.env.example \
     deployments/multi-dev/instances/alpha.env
   cp deployments/multi-dev/instances/beta.env.example \
     deployments/multi-dev/instances/beta.env
   ```

2. Bootstrap each instance database and Temporal namespace exactly as for a
   per-instance container. Repeat this block for each manifest before starting
   the zygote.

   ```bash
   set -a
   . deployments/multi-dev/instances/alpha.env
   set +a

   docker compose -f deployments/multi-dev/docker-compose.infra.yml exec -T postgres \
     createdb -U postgres "$TRACECAT_INSTANCE"
   docker compose -f deployments/multi-dev/docker-compose.infra.yml exec -T temporal \
     temporal operator namespace create \
       --address localhost:7233 \
       --namespace "$TEMPORAL__CLUSTER_NAMESPACE"
   ```

3. Validate the manifests with real forks, then build and start the one zygote
   container.

   ```bash
   TRACECAT__ZYGOTE_INSTANCE_DIR=deployments/multi-dev/instances \
     uv run python -m tracecat.zygote --dry-run

   docker compose \
     -f deployments/multi-dev/docker-compose.zygote.yml \
     up -d --build
   ```

The parent has no HTTP port of its own, so a single container healthcheck cannot
represent all children. Check each configured port directly, for example
`curl -f http://localhost:8100/health` and
`curl -f http://localhost:8101/health`.

To measure the saving, compare the one zygote cgroup with the same number of
per-instance containers:

```bash
docker stats --no-stream tracecat-zygote-zygote-1
```

The baseline standalone process was about 621 MiB in the original measurement.
Unlike summing N per-instance container readings, one zygote container's cgroup
counts its children’s shared copy-on-write pages once.

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
