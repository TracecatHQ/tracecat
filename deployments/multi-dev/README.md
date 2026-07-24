# Multi-instance development stacks

A **stack** is one lightweight Tracecat instance: a single container running the
API and all four Temporal workers in one Python process, against infrastructure
shared by every stack on the machine.

Stacks let you run several Tracecat instances at once — one per worktree, one
per agent, one per branch under review — without paying for a full
`docker-compose.dev.yml` cluster each time.

## Nothing here is per-stack

There are no stack directories and no per-stack files to create. A stack's
identity is derived from the **worktree** it is started in, so adding one is a
command, not a commit:

```bash
cd ~/dev/tracecat/trees/my-branch
just cluster --standalone up -d
```

That derives a stack id from the branch name and the globally allocated cluster
number (for example `my-branch-5`), then uses it for the database, the Temporal
namespace, all five bucket names, and the Redis database index. Secrets are
minted once into `~/.tracecat/stacks/<stack-id>.env` — outside the repo, never
committed.

Everything is driven by `scripts/cluster`. The two files here are assets it
consumes:

| File | Role |
| --- | --- |
| `infra/compose.yaml` | Shared Postgres, Temporal, MinIO, Redis. One project per machine (`tracecat-shared-infra`). |
| `stack.compose.yaml` | One parameterized stack. Every per-stack value is interpolated from the environment. |

## Usage

```bash
just cluster --standalone up -d      # start (or reuse) this worktree's stack
just cluster --standalone ports      # URLs, database, namespace, Redis index
just cluster --standalone ps         # container status
just cluster --standalone logs -f    # follow logs
just cluster --standalone down       # stop, keep all data
just cluster --standalone rm         # stop and delete this stack's volumes
```

`up` is self-contained. It starts the shared infra if it is not already
running, creates this stack's database and Temporal namespace if they do not
exist, then builds, starts, and seeds a dev user. Every step is idempotent, so
re-running `up` is safe.

Shared infrastructure has its own subcommand, because no single stack should
tear down what every other stack depends on:

```bash
just cluster infra ps
just cluster infra logs
just cluster infra down      # stop, keeping all data
just cluster infra nuke      # destroy it and EVERY stack's data (prompts)
```

## Ports

A stack publishes exactly one port. Everything else lives in shared infra on
fixed ports.

| | Port |
| --- | --- |
| Stack app + API | `10080 + (cluster_num - 1) * 100` (`80 + …` without portless) |
| Shared Postgres | 5442 |
| Shared Redis | 6389 |
| Shared Temporal | 7243 |
| Shared Temporal UI | 8243 |
| Shared MinIO | 9010 |
| Shared MinIO console | 9011 |

Each shared port sits 10 above the matching dev-cluster base. Dev clusters
stride by 100 from those same bases, so a shared port can never collide with a
cluster's. Both topologies also draw cluster numbers from one global pool, so a
stack and a dev cluster can never claim the same 100-block.

`scripts/cluster` exports these as `TC_SHARED_*` and `infra/compose.yaml` reads
them, so the script is the single source of truth rather than the two files
agreeing by convention.

## What a stack does not include

- **No UI.** A stack is API-only. To put a browser in front of one, run the
  frontend against it — but note that `frontend/next.config.mjs` sets
  `connect-src 'self'`, so the UI and the API must share an origin. Put them on
  one host behind a reverse proxy that strips `/api` (the repo-root `Caddyfile`
  is the pattern). A UI served from a different port than the API has every
  request blocked by CSP.
- **No LiteLLM.** Point `TRACECAT__LITELLM_BASE_URL` at one running elsewhere.
- **No nsjail sandbox.** Stacks run `TRACECAT__EXECUTOR_BACKEND=direct` with the
  executor in-process, which is why the sandbox compose overlay is skipped.

## Memory

Roughly 300 MiB for shared infra, once, plus one container per stack. A stack is
heaviest during its first boot, while it runs migrations and builds registry
artifacts, and settles substantially lower once those are cached.

Because infra is shared, the marginal cost of the next stack is just its own
container; the fixed cost amortizes across every stack on the machine.

## Memory and concurrency knobs

`stack.compose.yaml` uses small development defaults, all overridable from the
environment:

| Variable | Default | Effect |
| --- | ---: | --- |
| `MALLOC_ARENA_MAX` | `2` | Limits glibc allocator arenas and retained heap growth. |
| `TEMPORAL__THREADPOOL_MAX_WORKERS` | `4` | Caps each applicable worker activity thread pool. |
| `TEMPORAL__MAX_CONCURRENT_ACTIVITIES` | `8` | Caps DSL worker activities in flight. |
| `TEMPORAL__MAX_CONCURRENT_WORKFLOW_TASKS` | `4` | Caps DSL workflow tasks; keep this at least `2`. |

Lower concurrency first when the machine is memory- or CPU-constrained. The
combined process still creates separate activity thread pools for worker types,
but it imports the Tracecat package only once.

## Relationship to the fork supervisor

`tracecat/standalone_supervisor.py` forks N children from one warmed parent
process. It remains in the codebase but is not used by this path.

Measured across three instances, copy-on-write saved about 19 MiB per child,
while making per-child restart, health checks and resource limits impossible —
Docker only ever sees one container. One container per stack trades that ~19 MiB
for real per-stack lifecycle, which is the better deal at these instance counts.
