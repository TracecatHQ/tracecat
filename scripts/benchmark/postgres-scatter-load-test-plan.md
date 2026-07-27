# PostgreSQL scatter load-test plan

- Status: Proposed
- Last updated: 2026-07-27
- Target: An isolated local Tracecat cluster with a resource-constrained
  PostgreSQL container

## Decision this test should support

Establish a safe aggregate database-connection budget for high-fan-out
scatter/gather workloads, and verify that Tracecat queues or rejects excess work
in a bounded, recoverable way.

The headline product target: a burst of up to 10,000 concurrent actions,
spread across many workflows, should be absorbed by the Temporal task queue
and drained at a constant sustained rate on a small database, provided
executor concurrency is explicitly capped within the connection budget.

The first experiment should test direct PostgreSQL access. It should answer
whether explicit application pool sizing, action admission, and batching solve
the overload mode before introducing a database proxy. A local Docker test
cannot choose between RDS Proxy and PgBouncer or predict RDS throughput by
itself.

## Why this needs a dedicated test

The current controls apply at different scopes:

- `TRACECAT__DSL_SCHEDULER_MAX_PENDING_TASKS` defaults to 64 and limits pending
  tasks inside one workflow execution. Concurrent workflow executions each get
  their own allowance.
- Self-hosted deployments default the organization-scoped
  `max_concurrent_actions` permit to unlimited, and permit leasing is not yet
  activated in current deployments; the workflow skips permit activities when
  no limit is set.
- When enabled, an action permit waits with bounded backoff and defaults to a
  120-second maximum wait.
- Each executor worker process defaults to 100 concurrent activities. This is a
  per-process control and is multiplied by executor replicas.
- Each process that creates the shared async SQLAlchemy engine can currently
  grow from a pool size of 10 to 70 connections after overflow.
- `TRACECAT__DB_POOL_TIMEOUT` is defined, but the async engine does not currently
  pass it to SQLAlchemy.
- The development Compose stack has several database-capable services, while
  `postgres_db` has no CPU or memory limit and uses PostgreSQL's default
  connection settings.

The theoretical application connection ceiling is therefore deployment-wide,
not per workflow:

```text
C_app = sum(
  replicas[i]
  * processes_per_replica[i]
  * (pool_size[i] + max_overflow[i])
)
```

Pools are lazy, so this is a ceiling rather than a prediction of simultaneous
use. The test must record the actual baseline and peak connection counts.

Current implementation references:

- [scheduler and pool defaults](../../tracecat/config.py)
- [async SQLAlchemy engine construction](../../tracecat/db/engine.py)
- [per-workflow scheduler](../../tracecat/dsl/scheduler.py)
- [self-hosted tier defaults](../../tracecat/tiers/defaults.py)
- [organization permit semaphore](../../tracecat/tiers/semaphore.py)
- [executor worker capacity](../../tracecat/executor/worker.py)
- [development Compose stack](../../docker-compose.dev.yml)
- [workflow execution API](../../tracecat/workflow/executions/router.py)
- [table actions](../../packages/tracecat-registry/tracecat_registry/core/table.py)
- [numbered cluster wrapper](../cluster)

## Goals

- Reproduce database pressure from realistic Tracecat scatter/gather workflows.
- Find the concurrency at which the current configuration first degrades or
  fails.
- Separate database connection exhaustion from PostgreSQL CPU/memory pressure,
  executor saturation, and Temporal scheduling delay.
- Compare the current behavior with:
  - an explicit aggregate pool budget;
  - explicitly bounded executor capacity;
  - bulk table insertion.
- Verify correctness, bounded failure, and recovery as well as throughput.
- Produce a repeatable runner, machine-readable results, and a short decision
  record.

## Non-goals

- Predicting the throughput, latency, IOPS, burst-credit behavior, networking,
  failover, or Multi-AZ behavior of a particular RDS instance.
- Using production or customer-derived data.
- Proving that a proxy is required.
- Testing RDS Proxy locally. It is an AWS-managed service and needs a separate
  AWS canary if it remains a candidate.
- Generating one enormous workflow history merely to obtain a large action
  count. Multiple concurrent workflows are required to exercise aggregate
  admission and pool multiplication.

## Hypotheses

1. Multiple concurrent scatters can exceed a small PostgreSQL connection budget
   even though every individual workflow stays below its 64-task scheduler cap.
2. With the current pool defaults, the first visible failure may be a
   PostgreSQL connection-slot error or an application pool wait rather than a
   clean admission decision.
3. An explicit aggregate pool budget and bounded executor capacity will convert
   connection storms into bounded queuing for the tested topology.
   Organization-scoped action permit leasing is not activated in current
   deployments and is not required for this conversion.
4. `core.table.insert_rows` will achieve materially better database efficiency
   than scattering the same number of `core.table.insert_row` actions.
5. A proxy will not fix an unbounded work queue or an oversized aggregate
   application pool; it can only be evaluated after those controls are explicit.
6. With executor concurrency capped at or below the connection budget, a burst
   of roughly 10,000 actions spread across many workflows is absorbed by the
   Temporal task queue and drained at a constant sustained rate, with no
   additional database pressure beyond the capped steady state. This holds
   because actions carry only a start-to-close timeout (queue wait does not
   expire them) and workflow execution timeout defaults to unlimited.

## Safety and isolation

Run this only in a fresh numbered cluster owned by the load-test worktree.

- Check `docker compose ls --filter name=tracecat` and `just cluster list`
  before starting.
- Allocate a new Compose project, ports, PostgreSQL volume, Temporal database,
  and synthetic Tracecat workspace.
- Do not alter, stop, or reuse an existing developer cluster.
- Do not use `docker compose down -v`, `docker volume rm`, `just cluster rm`, or
  another volume-deleting command. Stop the test with `just cluster <n> down`
  and retain its volume until analysis is complete.
- Use only synthetic identifiers and payloads.
- Ramp load in steps. Do not begin with a thousands-wide scatter.
- Keep a PostgreSQL administrative connection reserve outside the application
  budget.

Abort a run when any of the following occurs:

- PostgreSQL, API, worker, or executor is OOM-killed or repeatedly restarts.
- The administrative connection reserve is consumed.
- Error or retry volume continues growing after the load generator stops.
- Host memory pressure threatens unrelated local workloads.
- The metric collector cannot observe the database during the run.

Stopping new work is the first response to an abort condition. Preserve logs
and the database volume; do not immediately tear the cluster down.

## Proposed test assets

Implementation should add the following:

1. `docker-compose.loadtest.yml`

   A Compose override that constrains only the test cluster. Initial PostgreSQL
   settings:

   ```yaml
   services:
     postgres_db:
       cpus: "1.0"
       mem_limit: 1g
       memswap_limit: 1g
       pids_limit: 256
       command:
         - postgres
         - -c
         - max_connections=50
         - -c
         - shared_buffers=128MB
   ```

   These are controlled test settings, not a claim that the container resembles
   a named RDS instance class. Before the first run, validate the merged Compose
   model and verify the effective cgroup and PostgreSQL settings from inside the
   running container.

   Constrain only `postgres_db`. Temporal persistence runs on the separate
   `temporal_postgres_db` service and must stay unconstrained so Temporal
   scheduling pressure is not accidentally coupled to the database under test.
   Verify at runtime that Temporal is not pointed at the constrained database.

2. Cluster-wrapper support for the override

   Extend `scripts/cluster` with a narrow `--compose-override <path>` or
   `--loadtest` option. It must preserve the wrapper's numbered-project,
   isolated-port, and isolated-volume behavior. Do not invoke raw Compose with a
   project name that could collide with another worktree.

3. A synthetic table and workflow fixture

   The table should contain:

   - `run_id`
   - `workflow_seq`
   - `branch_seq`
   - `payload`

   Add a unique index on `(run_id, workflow_seq, branch_seq)` so missing and
   duplicate writes are measurable.

   The adversarial workflow should fan out `core.table.insert_row` over
   `FN.range(...)`, then gather a compact result. It must not return all inserted
   payloads into workflow history.

   A control workflow should write the same logical rows with
   `core.table.insert_rows`.

4. An asynchronous API load runner

   The runner should:

   - authenticate as a synthetic local user;
   - start workflows through `POST /workflow-executions`, exercising the public
     admission path;
   - support workflow count, branch count, ramp duration, steady-state duration,
     payload size, and per-run timeout;
   - poll executions to terminal state at a fixed poll interval that is
     identical across all phases and recorded in the scenario configuration.
     Execution status is served from Temporal, but each poll request still
     performs auth and membership queries against the application database;
     holding the interval constant lets the baseline measurement absorb that
     overhead;
   - emit JSON Lines plus a human-readable summary;
   - stop submitting work on an abort signal without deleting fixtures.

   Do not start workflows by calling Temporal directly.

5. A metric collector

   Store run artifacts in a configurable directory outside tracked source by
   default, for example `/tmp/tracecat-scatter-load/<run-id>/`. Capture:

   - the resolved Compose configuration;
   - effective container limits and restart/OOM state;
   - effective PostgreSQL settings;
   - sampled PostgreSQL activity;
   - API, worker, executor, and PostgreSQL logs;
   - runner results and the exact scenario configuration;
   - the Tracecat commit and container image identifiers.

   The existing benchmark scripts reference a `/health/db-pool` endpoint that
   is not present in the current API. Do not make the new test depend on it.
   Either add supported, read-only pool instrumentation or limit the first
   version to PostgreSQL activity, service logs, and runner metrics.

## Connection budget

For each scenario, derive the application budget rather than copying an
arbitrary per-process setting:

```text
C_budget = max_connections - C_admin_reserve - C_nonload_baseline
C_configured = sum(
  replicas[i]
  * processes_per_replica[i]
  * (pool_size[i] + max_overflow[i])
)

Required: C_configured <= C_budget
```

Before choosing pool values:

1. Inventory every process that can create the async engine, including process
   workers inside a container.
2. Measure idle connections after the cluster reaches steady state.
3. Reserve connections for migrations, administration, and recovery. PostgreSQL
   already withholds `superuser_reserved_connections` (default 3) from
   non-superuser roles; state whether the administrative reserve is in addition
   to or inclusive of that built-in reserve.
4. Set `TRACECAT__DB_POOL_SIZE` and `TRACECAT__DB_MAX_OVERFLOW` on every
   applicable service in the load-test override.
5. Wire and test `TRACECAT__DB_POOL_TIMEOUT` so exhaustion fails within a known
   bound.

With `max_connections=50`, do not simply give each service a pool of 40.
Calculate one deployment-wide envelope. If the measured baseline or required
reserve makes 50 impractical for the full stack, increase it and record why;
the invariant matters more than the initial number.

## Workload shape

Use four stages for every scenario:

1. **Warm-up:** one small workflow to populate lazy pools and caches.
2. **Ramp:** add workflow executions gradually.
3. **Sustain:** hold the target concurrency long enough to distinguish a spike
   from a growing queue.
4. **Recovery:** stop submissions and observe return to the pre-run baseline.

Start with this ramp:

| Step | Concurrent workflows | Branches per workflow | Logical rows |
| ---: | ---: | ---: | ---: |
| 1 | 1 | 8 | 8 |
| 2 | 1 | 32 | 32 |
| 3 | 1 | 64 | 64 |
| 4 | 4 | 64 | 256 |
| 5 | 8 | 64 | 512 |
| 6 | 16 | 64 | 1,024 |

Stop escalating after the first abort condition. The scheduler-cap boundary can
be characterized later with a branch count above 64, but it must not replace
the multi-workflow cases.

Phase 5 extends the ramp toward the 10,000-action burst target, and runs only
after phase 3 passes at `8 x 64`:

| Step | Concurrent workflows | Branches per workflow | Logical rows |
| ---: | ---: | ---: | ---: |
| 7 | 32 | 64 | 2,048 |
| 8 | 64 | 64 | 4,096 |
| 9 | 156 | 64 | 9,984 |

The burst must be spread across workflows, never one giant scatter: the
per-workflow scheduler caps pending tasks at 64, and a single 10k-branch
workflow would approach Temporal's history-event limits. Submit the burst as
fast as the API accepts it, then measure queue depth, drain rate, and expiry
rather than end-to-end latency. Before the burst cells, verify that the
workspace has no workflow default timeout configured, since queue wait counts
against workflow execution timeout when one is set.

Run each completed matrix cell three times. Truncate the fixture table between
matrix cells rather than deleting by `run_id`, so dead tuples and autovacuum
activity from earlier cells cannot skew later repeats on the constrained
container; do not recreate the database volume. Record fixture table and index
size plus autovacuum activity as per-run artifacts so any residual drift is
visible.

## Scenario matrix

Organization-scoped action permit leasing is not activated in current
deployments (`max_concurrent_actions` defaults to unlimited, and the workflow
skips permit activities entirely when no limit is set). It is therefore not a
control in the primary matrix; it appears only as an optional follow-on phase
for when the feature activates.

| Phase | PostgreSQL | Application pools | Executor capacity | Write path | Purpose |
| --- | --- | --- | --- | --- | --- |
| 0 | Unconstrained local default | Current defaults | Current default | Single-row scatter | Validate the fixture and runner |
| 1 | Constrained | Current defaults | Current default | Single-row scatter | Characterize the current failure boundary |
| 2 | Constrained | Aggregate budget enforced | Current default | Single-row scatter | Isolate pool sizing and timeout |
| 3 | Constrained | Aggregate budget enforced | Explicit per-process bound | Single-row scatter | Validate bounded admission and recovery |
| 4 | Constrained | Aggregate budget enforced | Explicit per-process bound | Bulk insert | Measure the batching control |
| 5 | Constrained | Aggregate budget enforced | Explicit per-process bound | Single-row scatter | 10k-action burst absorption via the Temporal queue |
| 6, optional | Constrained behind PgBouncer | Same application budget | Same explicit bound | Both | Evaluate proxy compatibility and incremental value |
| 7, optional | Constrained | Aggregate budget enforced | Same explicit bound | Single-row scatter | Validate permit admission once leasing is activated |

Phase 6 is warranted only if phases 2-5 show a remaining connection-churn or
failover problem that a proxy could plausibly solve.

Phase 7 is warranted only once action permit leasing is activated in the
product. When it runs: the Redis action semaphore is keyed by organization, so
repeat the phase 3 target as two organizations with half the workflows each,
keeping total logical work constant, to check that per-organization limits are
not being mistaken for a deployment-wide database bound. Note the interaction
with bursts: permit acquisition runs as a separate activity with a 120-second
default maximum wait, so under a deep backlog a tight organization cap
converts queue absorption into permit-wait failures. Phase 7 must characterize
that behavior explicitly; the executor cap, not the permit, is the intended
burst throttle.

Executor capacity must be budgeted as:

```text
C_executor = executor_replicas * max_concurrent_activities_per_replica
```

Run retry behavior as a separate sub-scenario. The characterization pass should
use one action attempt where the workflow format permits it, so retries do not
hide the first failure boundary. The retry pass should then verify that the
configured policy does not amplify overload.

## Measurements

### Load generator

- Submitted, accepted, completed, failed, and timed-out workflow counts
- End-to-end throughput
- p50, p95, and p99 workflow latency
- Error class and first-failure timestamp
- Expected versus actual unique table rows

### PostgreSQL

Sample at least once per second:

- total connections versus `max_connections`
- active, idle, idle-in-transaction, and waiting sessions
- `wait_event_type` and `wait_event`
- longest transaction age
- transaction commit/rollback and deadlock deltas
- connection-slot, statement-timeout, and resource errors

Label connections by Tracecat service by setting a per-service
`application_name` on the engine (asyncpg accepts it through
`connect_args={"server_settings": ...}`), so `pg_stat_activity` sampling is
attributable from the first run. This is a small supported change and belongs
in the same preparatory work as the pool-timeout wiring; only fall back to
recording an instrumentation gap if that change cannot land in time.

### Tracecat and Temporal

- SQLAlchemy pool timeouts and checkout latency, if instrumented
- API 5xx responses and executor/action failures
- action retry counts
- workflow schedule-to-start and execution latency
- Temporal task-queue backlog
- workflow history event count and size

### Containers and host

- CPU and memory usage
- PostgreSQL disk I/O
- container restart count
- OOM state
- host memory pressure during the run

## Result classification

Classify every failed execution as one primary failure mode:

- admission rejection
- application pool timeout
- PostgreSQL connection-slot exhaustion
- PostgreSQL statement or lock timeout
- Temporal schedule-to-start delay
- executor saturation or action timeout
- process/container OOM or restart
- missing or duplicate table writes
- load-runner or observability failure

This prevents a generic timeout rate from hiding where backpressure actually
occurred.

## Initial acceptance criteria

Phase 1 is characterization and may fail. Its result is useful only if the
failure boundary and primary failure mode are observable and repeatable.

Phase 2 passes if pool exhaustion surfaces as application pool-timeout failures
within the configured `TRACECAT__DB_POOL_TIMEOUT` bound, with no PostgreSQL
connection-slot errors and application connections within the calculated
budget. Workflow failures are acceptable in phase 2; unbounded waits and
database-side slot errors are not.

For the bounded configuration in phase 3, the initial target is the `8 x 64`
cell:

- every target workflow is accepted and completes successfully within the
  scenario timeout;
- the table contains exactly one row for every successful logical branch;
- application connections remain within the calculated budget;
- no PostgreSQL connection-slot errors occur;
- no relevant container is OOM-killed or restarted;
- overload above the target is queued or rejected within a documented bound,
  without an unbounded retry storm;
- after submissions stop, connection count and queue depth return to the
  pre-run range within 60 seconds.

Phase 5 (burst absorption) passes at a given step when:

- every submitted workflow is accepted and eventually completes with exactly
  one row per logical branch — no expiry of queued work;
- database connections never exceed the capped steady state observed in
  phase 3, regardless of backlog depth;
- drain rate remains roughly constant from burst start to queue empty, rather
  than degrading as the backlog ages;
- Temporal task-queue backlog grows during submission and returns to zero
  after the drain, with the constrained PostgreSQL container showing no
  additional pressure attributable to backlog size.

The `2 organizations x 4 workflows x 64 branches` topology check moves to
phase 7 with the permit control it exercises.

The final latency and throughput thresholds must come from a product SLO or a
measured production-safe target. Do not invent them from local Docker timings.

Phase 4 passes if it preserves row correctness while using fewer action
executions and less database time per logical row than phase 3. Record the
ratio; do not use an undefined claim such as "significantly faster."

## Decision rules after the test

- If the current defaults fail and a bounded pool plus bounded executor
  capacity pass, prioritize those controls. A proxy is not required to fix the
  demonstrated failure mode, and activating permit leasing is not a
  prerequisite for it either.
- If both configurations exhaust CPU or I/O while connections remain within
  budget, the test found a database-capacity or query-efficiency limit, not a
  pooling decision.
- If batching moves the boundary substantially, prefer batching for large
  table writes before adding more scatter concurrency.
- If direct bounded connections work but recovery from connection churn or
  database failover remains poor, evaluate a proxy in a separate test.
- If PgBouncer is evaluated, test transaction-pooling compatibility with
  asyncpg/SQLAlchemy behavior, prepared statements, session state, migrations,
  advisory locks, and long transactions before measuring throughput.
- If RDS Proxy remains a candidate, run an AWS canary against a disposable RDS
  environment and collect proxy connection, borrow-latency, and session-pinning
  metrics. Do not extrapolate that decision from the local PgBouncer result.

## Implementation and execution order

1. Add and validate the Compose override and cluster-wrapper option.
2. Inventory DB-capable processes and calculate the connection envelope.
3. Add supported pool-timeout wiring, per-service `application_name`
   attribution, and any other observability needed by the test. These are
   product changes to `tracecat/db/engine.py`, not test scaffolding: land them
   as a reviewed pull request on `main` before executing phase 2 or later, so
   load-test conclusions are not built on unreviewed branch code.
4. Build the table, single-row workflow, and bulk control.
5. Build the runner and metric collector with abort handling.
6. Execute phase 0 and inspect row correctness and artifacts.
7. Execute phases 1-5 one ramp step at a time, entering the burst steps only
   after phase 3 passes at `8 x 64`.
8. Write a short result document containing the tested commit, environment,
   matrix, graphs, failure classifications, and recommendation.
9. Decide whether an AWS proxy canary or local PgBouncer phase is justified.

## Verification required for the implementation

- `docker compose -f docker-compose.dev.yml -f docker-compose.loadtest.yml config`
- Runtime checks for cgroup limits, `max_connections`, reserved connections, and
  effective pool settings
- A runtime check that Temporal persistence points at `temporal_postgres_db`,
  not the constrained `postgres_db`
- Focused tests for any `scripts/cluster` argument changes
- `shellcheck -x` for changed shell scripts
- `uv run ruff check` and `uv run ruff format --check` for changed Python files
- `uv run basedpyright <changed Python files>`
- A low-load end-to-end smoke run before any ramp

## Open decisions

- Which production-like service and process topology should the local cluster
  model?
- What per-executor-worker activity limit should be tested first? (The
  organization `max_concurrent_actions` question is deferred to optional
  phase 7, gated on permit leasing being activated in the product.)
- Is PostgreSQL activity plus logs sufficient for the first pass, or should
  pool checkout/wait metrics be implemented first?
- How does the runner authenticate: which auth mode does the test cluster run,
  and what credential type (session, API key, or service token) does the
  synthetic user hold? This gates building the runner.
- What product SLO supplies the final latency, throughput, and maximum queueing
  thresholds?
- After phases 1-4, is the remaining question connection reuse, failover
  behavior, or database capacity? That answer determines whether the next test
  is PgBouncer, RDS Proxy, or neither.
