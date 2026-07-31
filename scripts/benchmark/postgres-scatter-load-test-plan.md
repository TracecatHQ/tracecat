# PostgreSQL scatter load-test plan

- Status: Tooling implemented; experiment pending
- Last updated: 2026-07-30
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
- `TRACECAT__DB_POOL_TIMEOUT` is passed to SQLAlchemy, and each service labels
  its PostgreSQL sessions through `application_name`.
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

## Running the experiment

The operator interface treats a load test as a complete cluster workflow.
Define resource limits and workload dimensions in a CSV, validate it without
touching Docker, then execute it through the dedicated subcommand:

```bash
just cluster loadtest \
  --matrix packages/tracecat-benchmark/tracecat_benchmark/examples/matrix.example.csv \
  --dry-run

just cluster loadtest \
  --matrix packages/tracecat-benchmark/tracecat_benchmark/examples/matrix.example.csv
```

The subcommand owns fresh numbered-cluster allocation, row-by-row resource
application, workspace and monitor setup, collector/runner coordination,
fixture reset, and successful shutdown. It runs enabled rows in file order on
the same isolated project and retained database volume. `--case <case-id>`
selects individual rows; `--keep-cluster` keeps a successful cluster running.
Any failed cell stops the matrix and leaves the cluster running for diagnosis.

The lower-level `cluster up --loadtest`, collector, and runner commands below
document and test the implementation boundary. They are internal diagnostic
interfaces, not operator workflows.

## Implemented test tooling

The implementation includes the following:

1. `packages/tracecat-benchmark/docker-compose.loadtest.yml`

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
   a named RDS instance class. The collector validates the merged Compose model
   and captures the effective cgroup and PostgreSQL settings for every run.

   Constrain only `postgres_db`. Temporal persistence runs on the separate
   `temporal_postgres_db` service and must stay unconstrained so Temporal
   scheduling pressure is not accidentally coupled to the database under test.
   Verify at runtime that Temporal is not pointed at the constrained database.

2. Cluster-wrapper support for the override

   `scripts/cluster` provides a narrow `--compose-override <path>` and
   `--loadtest` layer. It preserves the wrapper's numbered-project,
   isolated-port, and isolated-volume behavior. Do not invoke raw Compose with a
   project name that could collide with another worktree.

   The matrix orchestrator uses this low-level lifecycle internally:

   ```bash
   ./scripts/cluster up -d --loadtest --new
   ```

   Operators should use `just cluster loadtest --matrix <csv>` instead. The raw
   command is retained for component diagnosis and tests.

   The wrapper rejects an auto-selected load-test `up` without `--new`, whether
   the file was selected through `--loadtest` or the generic
   `--compose-override` form. It therefore cannot silently reconfigure this
   worktree's running developer cluster. Passing an explicit cluster number
   remains available for deliberate restarts of the isolated load-test project.

### Experiment configuration

This PostgreSQL experiment uses the reusable
`tracecat_benchmark` harness. The runner selects a peer workload with
`--load-type`: `scatter` is the primary action fan-out, `bulk` is its batching
control, and `subflow` is workflow-level fan-out. New families such as agent
fan-out should be added as another load type without duplicating the runner,
collector lifecycle, or experiment controls.

`packages/tracecat-benchmark/tracecat_benchmark/examples/experiment.env.example`
is the authoritative
resource baseline and allowed-column registry for sparse CSV rows. It exposes
CPU and memory independently for every service in `docker-compose.dev.yml`, the
PostgreSQL-under-test settings, the workflow/executor throughput controls, and
every database-pool dimension used by the load-test override. Empty cells and
omitted resource columns inherit that baseline; unknown columns are rejected.

Copy and edit the example matrix, then validate the complete experiment before
starting Docker:

```bash
cp packages/tracecat-benchmark/tracecat_benchmark/examples/matrix.example.csv \
  /tmp/load-test-matrix.csv

just cluster loadtest --matrix /tmp/load-test-matrix.csv --dry-run
just cluster loadtest --matrix /tmp/load-test-matrix.csv
```

The orchestrator writes each row's effective resource configuration to a
temporary data-only environment file and gives that same file to the cluster
wrapper and collector. The collector stores the resolved CPU, memory, and
environment values in `compose_config.yml`; it also records effective cgroup
limits in `containers.json` and samples live use in `resource_usage.jsonl`.
Before publishing readiness, it compares the supplied ordered file list and
every rendered service configuration hash with the deployed containers. It
rejects a run if a file, environment value, or Compose layer changed after the
cluster started. For hash comparison and config capture, public app/API URLs
come from the deployed container environment, so a Portless deployment is not
rewritten to the runner's direct numbered localhost endpoint.

The primary throughput controls are:

| Process | Experiment variable | Baseline | Meaning |
| --- | --- | ---: | --- |
| worker | `TRACECAT__LOADTEST_DSL_SCHEDULER_MAX_PENDING_TASKS` | 64 | In-flight scheduler coroutines per workflow |
| worker | `TRACECAT__LOADTEST_CHILD_WORKFLOW_DISPATCH_WINDOW` | 16 | Concurrent child-workflow dispatches; valid range 8-128 |
| worker | `TRACECAT__LOADTEST_TEMPORAL_THREADPOOL_MAX_WORKERS` | 100 | Worker activity thread-pool size |
| worker | `TRACECAT__LOADTEST_TEMPORAL_MAX_CONCURRENT_ACTIVITIES` | 100 | Worker activity slots |
| worker | `TRACECAT__LOADTEST_TEMPORAL_MAX_CONCURRENT_WORKFLOW_TASKS` | 100 | Worker workflow-task slots |
| executor | `TRACECAT__LOADTEST_EXECUTOR_MAX_CONCURRENT_ACTIVITIES` | 2 | Executor activity slots in the constrained baseline |
| executor | `TRACECAT__LOADTEST_EXECUTOR_FOR_EACH_MAX_CONCURRENCY` | 4 | Per-action `for_each` fan-out |
| executor | `TRACECAT__LOADTEST_EXECUTOR_WORKER_POOL_SIZE` | backend default | Executor backend process pool |

Database pools are configured per database-capable service with
`TRACECAT__LOADTEST_<SERVICE>_DB_POOL_SIZE` and
`TRACECAT__LOADTEST_<SERVICE>_DB_MAX_OVERFLOW`. Pool timeout and recycle are
deployment-wide experiment variables:
`TRACECAT__LOADTEST_DB_POOL_TIMEOUT` and
`TRACECAT__LOADTEST_DB_POOL_RECYCLE`. The product defaults are 10 pool
connections, 60 overflow connections, a 30-second timeout, and a 600-second
recycle interval; the example file deliberately preserves the smaller
deployment-wide load-test budget described below.

Change one dimension at a time for attribution, or give a deliberate
multi-dimensional matrix row a descriptive `case_id`. Inspect the collector's
captured `compose_config.yml` and `containers.json` rather than re-rendering a
retained cluster without the matrix's temporary environment.

3. A synthetic table and workflow fixture

   The table contains:

   - `run_id`
   - `workflow_seq`
   - `branch_seq`
   - `payload`

   Missing and duplicate writes must be measurable via a unique constraint.
   The public tables API only supports single-column unique indexes, so the
   table carries a `dedupe_key` text column holding
   `<run_id>:<workflow_seq>:<branch_seq>` with a unique index on it, rather
   than a composite index on the three columns. Same measurability; it also
   permits `upsert` semantics on the insert actions if a scenario needs them.

   The adversarial workflow fans out `core.table.insert_row` over
   `FN.range(...)`, then gathers a compact result. It must not return all inserted
   payloads into workflow history.

   A control workflow writes the same logical rows with
   `core.table.insert_rows`.

   Give every checked-in workflow fixture a reserved alias. Fixture refresh may
   delete an existing workflow only when both that alias and the expected title
   match; a title collision by itself is not proof that the load-test harness
   owns the workflow.

4. An asynchronous API load runner

   The runner:

   - authenticate as a synthetic local user;
   - start workflows through `POST /workflow-executions`, exercising the public
     admission path;
   - support workflow count, branch count, ramp duration, steady-state duration,
     payload size, and a per-run timeout that bounds both admission and polling;
   - require an explicit run ID shared with the collector, and wait for that
     collector's readiness artifact before authenticating, provisioning
     fixtures, or submitting work;
   - poll executions to terminal state at a fixed poll interval that is
     identical across all phases and recorded in the scenario configuration.
     Execution status is served from Temporal, but each poll request still
     performs auth and membership queries against the application database;
     holding the interval constant lets the baseline measurement absorb that
     overhead;
   - emit JSON Lines plus a human-readable summary;
   - require the warm-up workflow to reach a known terminal state before the
     measured ramp begins, so unresolved warm-up work cannot exceed the
     requested concurrency or contaminate the experiment;
   - stop submitting work on an abort signal without deleting fixtures, and
     wake workers still waiting for their ramp stagger so the lifecycle marker
     is published promptly.

   Do not start workflows by calling Temporal directly.

5. A metric collector

   Store run artifacts in a configurable directory outside tracked source by
   default, for example
   `/tmp/tracecat-load-test/<run-id-fingerprint>/`. Capture:

   - the resolved Compose configuration;
   - effective container limits and restart/OOM state;
   - effective PostgreSQL settings;
   - sampled PostgreSQL activity;
   - sampled container CPU, memory, network, block I/O, process counts, and host
     CPU load and memory pressure;
   - safe aggregate diagnostics derived from time-scoped API, worker, executor,
     and PostgreSQL logs, never raw log lines;
   - runner results, post-measurement per-execution compact failure diagnostics,
     and the exact scenario configuration;
   - the Tracecat commit and container image identifiers.

   API, worker, executor, and PostgreSQL logs are required evidence. Optional
   service-log flags may add services to that set but must not replace any
   required service.

   A legacy executor benchmark references a `/health/db-pool` endpoint that is
   not present in the current API. This harness does not depend on it; the first
   version uses PostgreSQL activity, aggregate service-log diagnostics, and
   runner metrics.

   Require an explicit non-superuser monitoring DSN rather than assuming the
   deployment's PostgreSQL administrator credentials. The monitoring role needs
   `pg_read_all_stats`, `USAGE` on the selected workspace's `tables_*` schema,
   and `SELECT` on the synthetic fixture table. Pass the runner's workspace ID
   to the collector so row correctness is scoped to that exact schema. If
   PostgreSQL sampling fails, record the failure in the manifest and exit
   nonzero; that artifact set is not a valid experiment result.

   Require the runner's exact numbered-cluster API URL; never default to port
   80. Pass every ordered path from `scripts/cluster ... compose-files` to the
   collector, including `docker-compose.sandbox.yml` unless sandboxing was
   explicitly disabled. Also pass the selected cluster number and tenant mode.
   The collector must invoke Compose through `scripts/cluster` so the wrapper
   rebuilds the numbered ports, public URLs, sandbox backend, and tenant
   environment before capturing the resolved model or service-log diagnostics.

   Connect the collector to the exact numbered-cluster Temporal frontend.
   Sample `TEMPORAL__CLUSTER_QUEUE` as both a workflow queue and an activity
   queue because the DSL worker handles both partitions, and sample
   `TRACECAT__EXECUTOR_QUEUE` as an activity queue for action execution.
   Capture their approximate backlog count and age plus add/dispatch rates at
   the same cadence as PostgreSQL, writing `temporal_backlog.jsonl` through the
   recovery period. If either sampler takes longer than the requested interval,
   record a cadence failure and invalidate the experiment.
   Before publishing readiness, the collector validates the API, PostgreSQL,
   and Temporal host ports against `scripts/cluster ... ports`, then validates
   the Temporal namespace and both queue names against the wrapper-rendered
   Compose configuration. The ports lookup does not inherit the caller's API
   URL override, so a shared typo cannot make the runner and collector agree on
   the wrong cluster.
   Run PostgreSQL, Temporal, and resource sampling in independent loops so a
   slow Temporal RPC or `docker stats` call cannot hide a PostgreSQL peak.
   Resource sampling uses its own three-second cadence because a real
   `docker stats --no-stream` snapshot can take just over two seconds; a
   capture that exceeds that cadence still invalidates the experiment.
   Snapshot the worker and executor Temporal SDK Prometheus endpoints after
   warm-up and immediately after measured load. Retain baseline-to-final
   activity counter and histogram deltas, including retry-aware queue-level
   schedule-to-start timing. After recovery sampling ends, fetch the measured
   root and child workflow histories and aggregate completed Activities/s,
   decoded Tracecat Actions/s, outcomes, retries, schedule-to-start,
   start-to-close, and schedule-to-close latency by activity/action type.
   History reads must not compete with the measured workload. Raw workflow
   execution IDs stay in a mode-0600 temporary runner/collector handoff and are
   deleted after aggregation; retained evidence contains only aggregates and
   fingerprints.
   After the measured ramp/sustain window closes, fetch the public API's
   compact failure events for each failed execution. Retain only their
   structured action reference/name, event type/status, machine-readable
   Temporal failure type, child execution ID, and loop index in a record
   correlated by execution ID; never retain free-form error messages. This
   diagnostic fetch must not inflate the measured throughput or latency window.

   Start the collector first with a shared `--run-id`. The raw value remains
   process-local for workflow inputs and fixture-row correlation. Retained
   artifact fields and the artifact directory use its stable SHA-256
   fingerprint so user-provided labels cannot enter shareable evidence. The
   collector writes
   `collector_ready.json` only after the first PostgreSQL, Temporal, and runtime
   resource samples are durable. The record includes the normalized cluster
   number, public API URL, and a SHA-256 workspace fingerprint. The raw
   workspace ID remains process-local: the runner computes the same fingerprint
   and rejects readiness from any different target before it performs API work.
   The collector independently bounds connection setup, PostgreSQL settings
   capture, and the first sample from every required signal with one readiness
   deadline. It writes a failed manifest when that deadline expires, so a
   runner timeout cannot leave an orphaned collector.
   Retained correctness, table-drift, scenario, readiness, and manifest
   artifacts likewise use the fingerprint rather than the physical
   `tables_<workspace-id>` schema name. Service logs emitted since the
   collector's recorded start time are reduced in memory to fixed
   overload-signal counts and line totals; raw lines are never retained in
   experiment artifacts. The collector always queries the checked-in
   `scatter_load_rows` fixture and exposes no table-name override, so a
   user-provided label cannot enter correctness or drift evidence.
   Worktree-derived Compose project and generated container names are excluded:
   project identity is fingerprinted, while runtime records retain only the
   fixed service name and immutable image ID. Absolute Compose host paths under
   the repository are normalized beneath `<repo>`; other absolute host paths
   are redacted. Retained artifact references use `<artifact-root>` plus the run
   fingerprint and relative filename rather than serializing the configurable
   host directory. Compose rendering continues in a background thread while
   the sampling loops run, so environment capture cannot hide a short burst.

   The runner atomically writes `runner_complete.json` only after closing its
   artifact handles. The collector watches that marker instead of guessing a
   wall-clock duration, then continues sampling for `--recovery-seconds` (60
   seconds by default) before it captures final correctness, drift, container
   state, and logs. If the runner exits before it can publish the marker, stop
   the collector with SIGINT; incomplete runner artifacts make that experiment
   invalid.

   On a fresh cluster, the matrix orchestrator bootstraps the synthetic
   workspace through the public API, provisions the monitor, and passes the
   workspace ID and monitor DSN directly to the collector and runner. The
   underlying runner and provisioner modes remain independently callable for
   component diagnosis, but they are not part of the normal operator workflow.

   The provisioning DSN must use the same database role that creates Tracecat
   workspace tables. It needs permission to create/grant the monitor role and
   create/grant access in that workspace schema; it need not be a superuser.
   The command creates the physical `tables_*` schema before the collector
   starts, creates or updates a synthetic login, grants `pg_read_all_stats`,
   current schema/table access, and default `SELECT` privileges for the fixture
   table created later by the runner. The collector sets the workspace RLS
   context transaction-locally when it counts fixture rows. The generated DSN
   remains process-local and is never written to a tracked file or benchmark
   artifact.

   Every run-ID fingerprint owns a fresh artifact directory. The collector
   refuses a nonempty directory, and the runner refuses existing runner
   artifacts, so an interrupted or repeated invocation cannot combine
   experiments.

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
   to or inclusive of that built-in reserve. Caveat: the built-in reserve only
   protects against non-superuser roles, and the dev stack connects as the
   `postgres` superuser — so either run the test with a non-superuser
   application role or treat the built-in reserve as advisory and verify the
   operator can still connect at saturation.
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

During ramp and sustain, a slot is replenished only after its prior workflow is
known to be terminal. A local polling timeout or ambiguous submission transport
failure retires that slot, because its workflow may still be running and
reusing the slot would exceed the requested concurrency.

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

Run every phase 5 cell with the runner's `--one-shot` flag. In this mode,
`workflow_count` is the total number of parent workflows submitted, ramp time
controls how quickly that fixed burst is admitted, and a completed workflow is
not replenished during the drain window.

The burst must be spread across workflows, never one giant scatter: the
per-workflow scheduler caps pending tasks at 64, and a single 10k-branch
workflow would approach Temporal's history-event limits. Submit the burst as
fast as the API accepts it, then measure queue depth, drain rate, and expiry
rather than end-to-end latency. Before the burst cells, verify that the
workspace has no workflow default timeout configured, since queue wait counts
against workflow execution timeout when one is set.

Set `repeats=3` for each completed matrix cell. Before every repeat after the
first and before each subsequent cell, the orchestrator resets the fixture
relation through the public API. The reset verifies that the exact
`scatter_load_rows` table still matches the checked-in synthetic schema, then
deletes and recreates only that table. Recreating the relation removes retained
rows, dead tuples, and index drift so autovacuum activity from earlier cells
cannot skew later repeats on the constrained container. It does not grant write
access to the monitoring role and does not recreate the database volume.
Fixture table/index size and autovacuum activity remain per-run artifacts so
any residual drift is visible.

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
| 8, follow-on | Constrained | Aggregate budget enforced | Same explicit bound | Single-row insert via child workflows | Characterize subflow fan-out as a distinct dimension |

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

Phase 8 runs after the primary matrix and burst phases conclude. It replaces
the action-level scatter with workflow-level fan-out: a parent workflow
scatters `core.workflow.execute` over its branches, and each child workflow
performs one logical insert. This is a deliberately different stress shape —
it exercises workflow-execution throughput, DSL worker capacity, Temporal
scheduling, and per-parent history growth rather than raw connection pressure,
since each logical row still costs one executor activity. Keep total logical
rows equal to a completed phase 3 or phase 5 cell so the per-logical-row cost
of subflow fan-out is directly comparable with action scatter. It uses the
checked-in parent/child workflow fixture pair. Findings about child-workflow
admission, scheduler-cap interaction with pending child executions, and history
growth should be recorded in the same result document.

For workloads whose executor activities use `for_each`, database-reaching
executor concurrency must be budgeted as:

```text
C_executor = executor_replicas
  * max_concurrent_activities_per_replica
  * max_for_each_concurrency_per_activity
```

For actions without `for_each`, use a multiplier of one. Keep this bound below
the executor's connection-pool ceiling with enough headroom for
registry/bookkeeping queries.

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

Tracecat services label engine connections through the PostgreSQL
`application_name` setting, so `pg_stat_activity` samples are attributable from
the first run. The collector uses separate labels for its monitoring and
readiness-probe connections.

### Tracecat and Temporal

- SQLAlchemy pool timeouts and checkout latency, if instrumented
- API 5xx responses and executor/action failures
- completed Temporal Activities/s and decoded Tracecat Actions/s by type
- activity completion, failure, timeout, cancellation, and retry counts
- successful-activity p50, p95, and p99 schedule-to-start, start-to-close, and
  schedule-to-close latency
- retry-aware Temporal SDK schedule-to-start histograms at task-queue scope
- Temporal task-queue backlog
- workflow history event count and size

Temporal task-add and task-dispatch rates describe queue movement, not
completion throughput. Use history-derived completed Actions/s for the drain
rate that reached successful action completion.

### Containers and host

- CPU and memory usage
- PostgreSQL disk I/O
- container restart count
- OOM state
- host memory pressure during the run

## Result classification

Classify every failed execution as one primary failure mode:

- admission timeout or rejection
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

## Remaining execution order

The Compose override, cluster integration, fixtures, runner, collector, and CSV
orchestrator are implemented. The experiment still needs to:

1. Inventory DB-capable processes and calculate the connection envelope for
   each matrix configuration.
2. Run `just cluster loadtest --matrix <csv> --dry-run`.
3. Execute the low-load smoke case and inspect row correctness, captured
   Compose configuration, effective limits, PostgreSQL settings, and Temporal
   persistence isolation.
4. Execute phases 1-5 one ramp step at a time, entering the burst steps only
   after phase 3 passes at `8 x 64`.
5. Write a short result document containing the tested commit, environment,
   matrix, graphs, failure classifications, and recommendation.
6. Decide whether an AWS proxy canary or local PgBouncer phase is justified.
7. Run the checked-in phase 8 parent/child workflow fixtures against the same
   bounded configuration and compare per-logical-row cost with action scatter.

## Verification before the experiment

- `just cluster loadtest --matrix <csv> --dry-run`
- Focused tests for `scripts/cluster` and the load-test matrix lifecycle
- `shellcheck -x scripts/cluster`
- `uv run ruff check <changed Python files>`
- `uv run ruff format --check <changed Python files>`
- `uv run basedpyright <changed Python files>`
- A low-load end-to-end smoke run that verifies cgroup limits,
  `max_connections`, reserved connections, effective pool settings, and that
  Temporal persistence points at `temporal_postgres_db`

## Resolved implementation decisions

- Runner authentication uses cookie sessions via `POST /auth/login` as the
  cluster-seeded synthetic user by default, with a service-account API key
  taking precedence when provided. The internal service-key header is not
  accepted on the executions routes. Session auth means every poll request
  also reads the token row from the database under test.

## Open experiment decisions

- Which production-like service and process topology should the local cluster
  model?
- What per-executor-worker activity limit should be tested first? (The
  organization `max_concurrent_actions` question is deferred to optional
  phase 7, gated on permit leasing being activated in the product.)
- Is PostgreSQL activity plus logs sufficient for the first pass, or should
  pool checkout/wait metrics be implemented first?
- What product SLO supplies the final latency, throughput, and maximum queueing
  thresholds?
- After phases 1-4, is the remaining question connection reuse, failover
  behavior, or database capacity? That answer determines whether the next test
  is PgBouncer, RDS Proxy, or neither.
