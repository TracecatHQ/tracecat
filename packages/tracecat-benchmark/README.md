# Tracecat benchmarks

`tracecat-benchmark` is an internal uv workspace package. It owns the reusable
runner, collector, fixtures, artifact models, matrix orchestration, and
`docker-compose.loadtest.yml` override while the repository's `scripts/cluster`
command remains the operator-facing adapter for Compose and numbered-cluster
lifecycle.

Run local workflow load tests through the cluster subcommand:

```bash
just cluster loadtest \
  --matrix packages/tracecat-benchmark/tracecat_benchmark/examples/matrix.example.csv \
  --dry-run

just cluster loadtest \
  --matrix packages/tracecat-benchmark/tracecat_benchmark/examples/matrix.example.csv
```

The subcommand is a thin wrapper around the directly runnable Python CLI:

```bash
uv run --all-packages tracecat-benchmark \
  --matrix packages/tracecat-benchmark/tracecat_benchmark/examples/matrix.example.csv
```

Run the package-local test suite with:

```bash
uv run --all-packages pytest packages/tracecat-benchmark/tests
```

## Existing OrbStack Kubernetes deployment

Use the Kubernetes adapter to benchmark the already-running local deployment
without changing its Helm values or resource requests. The adapter requires the
active context to be exactly `orbstack` and checks that the API, workflow
worker, and executor deployments are available before it creates fixtures or
submits load.

Resolve or create the synthetic workspace, then run a small one-shot workload:

```bash
export TRACECAT_LOADTEST_EMAIL='benchmark@tracecat.example'
export TRACECAT_LOADTEST_PASSWORD='<local synthetic password>'

benchmark_workspace_id="$(
  uv run --all-packages tracecat-benchmark-kubernetes -- \
    --bootstrap-workspace
)"

uv run --all-packages tracecat-benchmark-kubernetes -- \
  --workspace-id "$benchmark_workspace_id" \
  --run-id orbstack-smoke \
  --case-id orbstack-smoke \
  --load-type bulk \
  --workflow-count 2 \
  --branch-count 4 \
  --ramp-seconds 0 \
  --steady-state-seconds 0 \
  --one-shot
```

The adapter uses `https://tracecat.k8s.orb.local/api` by default and loads the
OrbStack development CA from the macOS keychain for normal TLS verification.
Pass wrapper options before `--` (for example, `--context` or `--namespace`)
and runner options after it. Supply credentials for a synthetic user that
already has permission to create or use the selected workspace. This mode
exercises the public API and writes scenario, execution, and latency artifacts.
Its scenario records
`"evidence_mode": "runner_only"`: it does not claim the PostgreSQL, Temporal,
container, SQLAlchemy, or direct row-correctness evidence produced by the
Compose-only collector.

## Scatter burst matrix

`tracecat_benchmark/examples/scatter-burst.csv` defines a two-by-two burst
matrix:

| Independent action burst | Workflow shape | PostgreSQL profile |
| ---: | ---: | --- |
| 1,024 | 1,024 × 1 | 1 CPU / 1 GiB |
| 2,048 | 2,048 × 1 | 1 CPU / 1 GiB |
| 1,024 | 1,024 × 1 | 2 CPU / 4 GiB |
| 2,048 | 2,048 × 1 | 2 CPU / 4 GiB |

Every case is a one-shot burst of independent workflows admitted over one
second. Each workflow contains exactly one `core.table.insert_row` action and
does not use `for_each`. Completed workflows are not replenished, and the
runner continues polling until each workflow finishes or reaches its per-run
timeout. The 1 CPU / 1 GiB PostgreSQL profile uses 50 connections and 128 MiB
of shared buffers; the 2 CPU / 4 GiB profile uses 100 connections and 1 GiB of
shared buffers.

The non-database profile is held constant across all four cases: 2 CPU / 2 GiB
for the API, 2 CPU / 4 GiB each for the self-hosted Temporal server and its
PostgreSQL database, and 4 CPU / 4 GiB each for the workflow worker and
executor. Worker scheduling remains bounded at 64 pending DSL tasks per
workflow and 100 Temporal activity/workflow-task slots. The scatter matrix
overrides the executor admission limit to eight independent async action
activities. The executor's synchronous Temporal thread pool stays at 100 but
does not gate `execute_action_activity`. With no inner `for_each`, the
nine-connection executor main-pool ceiling can cover all eight admitted
database-reaching actions. The executor also has a separately budgeted
five-connection authentication pool; sandbox worker processes reach both
through the executor-local Action Gateway and do not create their own pools.

The API pool is sized separately because the one-second burst also exercises
workflow admission and status polling. The 1 CPU / 1 GiB PostgreSQL cells use a
5+0 API main pool and a 2+0 API auth pool. Counting every service's main and
auth pool, the one-replica profile has a theoretical application ceiling of 42
under PostgreSQL's 50-connection limit. The 2 CPU / 4 GiB cells retain the
24+16 API main-pool stress setting plus the two-connection auth pool, raising
the aggregate ceiling to 77 under PostgreSQL's 100-connection limit. Both
profiles use a 60-second acquisition timeout so saturation is bounded and
attributable.

The 1k and 2k labels describe actions scheduled in the burst, not simultaneous
database sessions. Temporal absorbs the backlog while the bounded executor
drains it. Each case starts with one repeat so the failure boundary can be
characterized before increasing `repeats` to three.

The disabled `scatter-burst-1k-pg-2c4g-exec32` case is an intentionally unsafe
opt-in pool-pressure cell. It raises executor activity admission from 8 to 32
and raises the executor main pool from 6+3 to 24+8 while retaining the separate
five-connection auth pool. Its configured application ceiling consumes all 100
PostgreSQL slots before operational reserve, so it is not a deployment profile.

The disabled `scatter-burst-1k-pg-2c4g-exec3` case instead scales out to three
executor replicas. Each replica retains 4 CPU / 4 GiB, eight activity slots,
and a 6+3 main pool plus a 5+0 auth pool. The service therefore admits up to 24
actions and has a 42-connection executor client-pool ceiling in aggregate.
Including the other services, the direct-PostgreSQL application ceiling is 105
of 100 connections, so this cell requires PgDog or a larger PostgreSQL budget
to be safe. The collector discovers and aggregates each replica's process-local
Temporal SDK metrics.

Preview or run the complete matrix:

```bash
just cluster loadtest \
  --matrix packages/tracecat-benchmark/tracecat_benchmark/examples/scatter-burst.csv \
  --dry-run

just cluster loadtest \
  --matrix packages/tracecat-benchmark/tracecat_benchmark/examples/scatter-burst.csv
```

Use `--case scatter-burst-1k-pg-1c1g` (or another case ID from the preview) to
run a single cell.

For example, run only the disabled three-replica comparison with:

```bash
just cluster loadtest \
  --matrix packages/tracecat-benchmark/tracecat_benchmark/examples/scatter-burst.csv \
  --case scatter-burst-1k-pg-2c4g-exec3
```

## PgDog transaction-pooling experiment

PgDog is an opt-in layer so the normal matrix remains a direct-PostgreSQL
baseline. With `--pgdog`, the API, migrations, and metric collector continue to
use PostgreSQL directly, while the worker, executors, and auxiliary services
connect to PgDog in transaction mode:

```text
Tracecat SQLAlchemy pools → PgDog transaction pool → PostgreSQL
```

The checked-in profile gives PgDog 1 CPU / 512 MiB and partitions PostgreSQL
connections into independent service-class pools: worker 6, executor 16, and
auxiliary services 2. The direct API has a 5+0 main pool and a 2+0 auth pool.
Together they cap application traffic at 31 PostgreSQL connections before the
operational reserve. The three executors can expose up to 27 main-pool and 15
auth-pool client connections, so PgDog can multiplex their transactions over
the 16-backend executor reserve without letting auxiliary traffic consume it.

The default 6/16/2 split preserves the measured direct-database throughput for
this 4-vCPU, three-executor action mix. Disabled matrix rows retain explicit
8- and 12-backend executor calibrations: 8 was proxy-queue bound, while 12 was
the connection-efficiency knee. Select the smallest named pool that meets the
checkout-wait and throughput SLO instead of comparing unlabeled runs.

The disabled `pgdog-1k-exec6-pg-1c1g-pool24` case tests the next scaling step:
six 4-vCPU executors, 48 admitted activities, and 24 executor backends against
the same 1,024-action burst and 1-vCPU / 1-GiB PostgreSQL instance. Run it only
where at least 24 host CPUs are available to the executors; Compose CPU quotas
cannot create physical capacity, and oversubscribing a smaller host invalidates
the throughput comparison.

The disabled `pgdog-1k-exec3-pg-1c1g-pool32-pgmax64` case isolates a larger
executor backend pool. PostgreSQL remains at 1 CPU / 1 GiB, while its connection
ceiling rises to 64 so the 32-backend experiment retains an operational reserve.

The checked-in profile currently leaves the API direct so the worker/executor
pooling experiment has one fewer variable. Earlier failed trials included an
irregular long-transaction observation, but the retained aggregate artifact
cannot attribute its oldest transaction to the API. A controlled follow-up
should give the API its own PgDog pool and hold runner and SQLAlchemy limits
constant; migrations and the collector should remain direct control paths.

Run the two-cell PgDog matrix with:

```bash
just cluster loadtest \
  --matrix packages/tracecat-benchmark/tracecat_benchmark/examples/pgdog-scatter.csv \
  --pgdog
```

For an exact direct-versus-proxy control, run the same case once without the
flag and once with it:

```bash
just cluster loadtest \
  --matrix packages/tracecat-benchmark/tracecat_benchmark/examples/pgdog-scatter.csv \
  --case pgdog-1k-exec3-pg-1c1g-pool16

just cluster loadtest \
  --matrix packages/tracecat-benchmark/tracecat_benchmark/examples/pgdog-scatter.csv \
  --case pgdog-1k-exec3-pg-1c1g-pool16 \
  --pgdog
```

Both cells submit four parent workflows containing 256 independent, statically
materialized `core.table.insert_row` actions through three executor replicas.
The generated workflow contains no runtime `for_each`. This keeps the 1,024
action target while removing 1,020 API admissions from the executor/database
measurement. The first cell holds PostgreSQL at 2 CPU / 4 GiB with 100
connection slots. The second reduces PostgreSQL to 1 CPU / 1 GiB with 50 slots,
while retaining the same PgDog and application configuration. This establishes
whether transaction multiplexing reduces backend sessions and whether the
smaller database preserves action throughput; it does not make PgDog a
substitute for bounded workflow or activity admission.

To isolate fixed workflow and action-lifecycle overhead from database-write
work, run the matched orchestration matrix:

```bash
just cluster loadtest \
  --matrix packages/tracecat-benchmark/tracecat_benchmark/examples/orchestration-overhead.csv \
  --pgdog
```

It runs two otherwise identical 4 × 256 cases. The control uses
`core.table.insert_row`; the no-op case uses 1,024 statically materialized
`core.transform.reshape` actions whose argument is the literal `null`. The
reshape actions contain no expressions and no `for_each`, and expect zero
fixture rows. Their activity metrics therefore measure the normal DSLWorkflow,
Temporal, executor, sandbox, and Action Gateway lifecycle with negligible
action implementation work. This is an orchestration control, not a claim that
the remaining cost belongs to any one of those components.

PgDog exposes health information on its internal port 8080 and OpenMetrics on
9090. Its load-test configuration is under `tracecat_benchmark/pgdog/`, and the
optional Compose layer is `docker-compose.pgdog.yml`.

The command validates the complete CSV before touching Docker. It then:

1. creates one fresh numbered cluster with the load-test Compose override;
2. applies each enabled row's resource configuration;
3. waits for the metric collector's preflight and initial samples before
   starting the workload runner;
4. resets the synthetic fixture between repeats and matrix cells;
5. reconfigures the same isolated cluster for the next row; and
6. stops the cluster after success while retaining its volumes.

If a command, runner, or collector fails, the matrix stops and leaves the
cluster running for diagnosis.

The cluster runs detached. Compose startup, reconfiguration, and shutdown
output is written to a matrix-level `orchestration.log` below the artifact
root instead of being replayed into the load-test terminal. Runner and
collector stdout/stderr are written to separate per-run files below that same
matrix log directory. The terminal shows phase changes, artifact locations,
and completed-run summaries. On failure it also shows a bounded tail of the
relevant log while retaining the complete output on disk.

Run evidence remains in its existing fingerprinted artifact directory. Process
logs are intentionally stored beside, rather than inside, that directory
because the collector atomically claims an empty artifact directory to prevent
accidental evidence reuse or overwrite.

Use `--dry-run` as the matrix preview. It prints every row as `RUN`, `DISABLED`,
or `FILTERED`, followed by the selected rows' resource overrides, without
touching Docker. `Workflows × branches` describes the workload shape, while
`Ramp / sustain` shows the ramp-up and steady-state durations. A `sustained` run
replenishes completed workflow slots through the sustain window; a `one-shot`
run submits the configured workflow count once without replenishing it.

Use `--case <case_id>` to run a specific row, including a disabled row. Repeat
the option to select multiple rows. Use `--keep-cluster` to leave a successful
cluster running.

## CSV schema

Every matrix requires a unique `case_id`. Empty workload cells use these
defaults:

| Column | Default |
| --- | ---: |
| `enabled` | `true` |
| `load_type` | `scatter` |
| `workflow_count` | `1` |
| `branch_count` | `1` |
| `ramp_seconds` | `10` |
| `steady_state_seconds` | `60` |
| `payload_bytes` | `256` |
| `run_timeout_seconds` | `300` |
| `poll_interval_seconds` | `1` |
| `collector_ready_timeout_seconds` | `120` |
| `sample_interval_seconds` | `0.5` |
| `recovery_seconds` | `60` |
| `warmup` | `true` |
| `one_shot` | `false` |
| `abort_stops_polling` | `false` |
| `max_connections` | `200` |
| `repeats` | `1` |

Resource columns use the exact `TRACECAT__LOADTEST_*` names from
`experiment.env.example`. Columns may be omitted, and empty cells inherit that
checked-in baseline. Unknown columns are rejected so typos cannot silently
change an experiment.

`workflow_count` is the number of replenished concurrency slots during a
sustained run. When `one_shot=true`, it is instead the exact number of parent
workflows admitted during the burst.

For `load_type=scatter`, fixture setup statically materializes `branch_count`
independent `core.table.insert_row` nodes in each workflow definition. It does
not use runtime `for_each`; the action target is `workflow_count × branch_count`.
Use `branch_count=1` with a large `workflow_count` to benchmark API admission,
or a small `workflow_count` with a larger `branch_count` to isolate workflow,
executor, and database throughput. Other workload types use `branch_count` to
construct their respective control workloads.

For `load_type=noop`, fixture setup similarly materializes `branch_count`
independent `core.transform.reshape` nodes with a literal `null` value. It has
no action expressions, runtime `for_each`, or fixture-table writes, so the
runner reports zero expected rows while retaining activity-level throughput and
latency measurements.

Artifacts are written below `/tmp/tracecat-load-test/`. The collector records
the resolved Compose model and rejects configuration drift between cluster
startup and measurement.

The load-test Compose layer injects SQLAlchemy pool instrumentation from this
package into the API, worker, and executor Python processes. Its bootstrap path
and internal metrics port are fixed harness wiring, not supported application
configuration or matrix tuning controls. Outside that Compose layer, Tracecat
uses its normal SQLAlchemy pool implementation and exposes no pool-metrics
endpoint.

## Results viewer

Browse recorded runs in a local browser UI:

```bash
uv run --all-packages tracecat-benchmark-viewer
```

It serves `http://127.0.0.1:8321` over `/tmp/tracecat-load-test/` and is
read-only. Use `--artifact-root`, `--port`, and `--host` to point it elsewhere.

The landing view lists every indexed run newest first, labeled with the matrix
`case_id` test slug and filterable by matrix and load type. The hashed run ID
remains the unique identifier. Opening a run shows its scenario configuration and measured
PostgreSQL settings, the runner summary and expected-versus-actual rows,
computed peak metrics, the history-derived activity metrics table, and the
sampled time series - PostgreSQL connections and transaction rates, Temporal
backlog depth, age, and flow rates, container CPU and memory per service, host
load and memory, and cumulative workflow submissions versus completions - all
aligned on the shared `monotonic` clock. Partial and aborted runs render with
whatever artifacts exist.

## Activity throughput and latency

Every completed matrix repeat prints a per-action/per-activity table. The
`Completed/s` column is actual completion throughput over that repeat's
measured interval; it is not Temporal's task-add or task-dispatch rate.
`core.*` action names are decoded from `execute_action_activity` inputs, while
Tracecat's internal Temporal activities remain grouped by their activity type.
If an action input cannot be decoded, its activity still appears, but it is
clearly marked and excluded from the decoded Tracecat Actions/s aggregate.

The collector brackets the measured interval with worker and executor
Prometheus snapshots, then waits until the recovery window has ended before
querying the measured root workflows and their child histories. History reads
therefore do not compete with the workload. Only activities scheduled inside
the measured interval are included. A completion after the interval is shown
as open rather than counted in throughput or latency.

The terminal summary reports:

- completed Tracecat Actions/s and all Temporal Activities/s;
- completed, failed, timed-out, canceled, and retried counts by action and
  activity type;
- p95 schedule-to-start, start-to-close, and schedule-to-close latency for
  successful completions.

Two machine-readable artifacts retain the full distributions:

- `activity_metrics.json` contains exact history-derived counts plus
  p50/p95/p99/min/max latency. History schedule-to-start excludes retried
  completions because Temporal history's original scheduled timestamp includes
  retry backoff. Schedule-to-close includes queueing and retry backoff. It also
  contains aggregate history byte sizes read directly from Temporal after
  recovery; workflow IDs are not retained in the artifact.
- `temporal_sdk_metrics.json` contains measured-interval counter and histogram
  deltas from the Temporal SDK. Its schedule-to-start timing is retry-aware and
  queue-level, so it cannot be attributed to an individual Tracecat action.
  Histogram percentiles are reported as bucket upper bounds. These deltas cover
  all work handled by the isolated worker processes during the interval;
  history metrics are the execution-scoped source.

`temporal_backlog.jsonl` remains the source for queue backlog, age, add rate,
and dispatch rate. Dispatch rate means tasks handed from a queue to workers;
use `Completed/s` in `activity_metrics.json` for successfully completed action
throughput.
