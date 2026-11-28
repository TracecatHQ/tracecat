# Staging burst replay (ENG-2613)

Replays the November incidents on staging with two test orgs, once with the
per-org limits off and once with them on, and measures what the steady org
sees. Org A is the noisy org; org B runs a steady workload. Baseline is org B
alone.

`burst.py` has four subcommands: `drive` (webhook starts from this machine),
`sample` (pods, memory and PgDog pools through kubectl), `collect` (Temporal
histories, read inside the DSL worker pod by `pod_collect.py`) and `report`
(percentiles and peaks). Every timing except API latency comes from server
timestamps in Temporal history.

## Metrics

| Metric | Source |
|---|---|
| Workflow start delay | WorkflowExecutionStarted (start accepted) → first WorkflowTaskStarted |
| Pacing delay | the start's `first_workflow_task_backoff` (start pacing, #4244); also reported excluded |
| Action schedule-to-start | ActivityTaskScheduled → ActivityTaskStarted, executor-queue activities of DSL runs |
| Tool-call schedule-to-start | the same, for `ExecuteRegistryToolWorkflow` runs (agent tool calls) |
| API latency | client-side webhook POST latency from this machine (includes network RTT) |
| Max action width | most executor activities of one org running at once |

Percentiles are nearest-rank. Report `n` with every row; a p99 needs about 200
samples to mean anything.

## Setup (once per environment)

1. Two orgs, each with a test workspace. Import `workflows/*.yml` with
   `POST /api/workflows?workspace_id=…` (multipart `file`), commit each, and set
   its webhook online (`PATCH /api/workflows/{id}/webhook {"status": "online"}`).
   - Both orgs: `burst_quick`, `burst_agent` (needs an agent model credential
     in the org).
   - Org A: `burst_lock`, `burst_lock_child` (alias `burst_lock_child`),
     `burst_scatter`, `burst_hold`.
2. Tables: `burst_read` (`k` INTEGER, `v` TEXT, 10 rows `k` = 0..9) in both
   workspaces; `burst_lock` (`k` INTEGER with `is_index: true`, `v` TEXT) in
   org A's.
3. Export each webhook URL in an env var (they contain the webhook secret;
   keep them out of files you commit):

   ```bash
   export BURST_A_QUICK=… BURST_A_LOCK=… BURST_A_SCATTER=… BURST_A_HOLD=… BURST_A_AGENT=…
   export BURST_B_QUICK=… BURST_B_AGENT=…
   export BURST_WORKSPACES='A=<org A workspace id>,B=<org B workspace id>'
   ```

4. kubectl access to the cluster (`--context`, default `tracecat-staging`).

Org limits are set with the admin tiers API
(`PATCH /api/admin/tiers/organizations/{org_id}`); limit caches are 30 s, so
wait 30 s after a change before starting a run.

## Running one scenario run

Each run goes in its own directory. Start the sampler, then the drive, then
collect once the runs have finished:

```bash
D=scripts/benchmark/staging_burst/out/s5-on; mkdir -p $D  # git-ignored
uv run python scripts/benchmark/staging_burst/burst.py sample --out $D/samples.jsonl --duration 600 &
uv run python scripts/benchmark/staging_burst/burst.py drive --out $D/requests.jsonl \
  --stream '{"label":"A-scatter","url_env":"BURST_A_SCATTER","rate":0,"count":1,"after":20,"payload":{"n":500,"secs":2}}' \
  --stream '{"label":"B-quick","url_env":"BURST_B_QUICK","rate":2,"count":360,"concurrency":10}' \
  --stream '{"label":"B-agent","url_env":"BURST_B_AGENT","rate":0.4,"count":72,"concurrency":10,"payload":{"calls":4}}'
# after every run has closed:
uv run python scripts/benchmark/staging_burst/burst.py collect --out $D/runs.jsonl --since 2026-11-22T00:00:00Z --until 2026-11-22T00:20:00Z
uv run python scripts/benchmark/staging_burst/burst.py report $D --out $D/report.md
```

Run output holds workflow and run IDs: keep it under the git-ignored
`scripts/benchmark/staging_burst/out/` (or outside the repo) and never commit
it. `collect` ships `pod_collect.py` from `--collector-ref` (default
`origin/main`; pass a reviewed SHA before this lands) and refuses contexts
without "staging" in their name.

Stream labels start with the org (`A-…`, `B-…`); `report` groups by that
prefix. `collect` lists every DSL and agent-tool run started in the two
workspaces in the window, so keep other work out of them during a run.

## Scenarios (as run on staging, 2026-11-22)

Sized for staging (1 executor with 100 activity slots and a 70-connection
pool, 1 DSL worker at 2 GiB, PgDog 2 × 70). Scale org A's counts with the
fleet. "Off" means org A's limits are null; scenario 1's off-run also needs the
per-org DB cap off and scenario 2's the default worker cache (chart values).

Org B's steady load, in every run including the baseline:

```text
{"label":"B-quick","url_env":"BURST_B_QUICK","rate":2,"count":360,"concurrency":10}
{"label":"B-agent","url_env":"BURST_B_AGENT","rate":0.4,"count":72,"concurrency":10,"payload":{"calls":4}}
```

Org A's load per scenario (same load off and on):

| # | Org A streams | Org A limits for the on-run |
|---|---|---|
| 1 lock pileup | `{"label":"A-lock","url_env":"BURST_A_LOCK","rate":0.05,"count":4,"after":15,"payload":{"rows":1000,"batches":200,"children":10}}` and `{"label":"A-agent","url_env":"BURST_A_AGENT","rate":0.1,"count":15,"after":15,"payload":{"calls":4}}` | actions 10, tool calls 4, agent turns 4; DB cap 4 |
| 2 webhook burst | `{"label":"A-hold","url_env":"BURST_A_HOLD","rate":0,"count":2000,"after":15,"concurrency":40,"payload":{"pad_bytes":120000}}` | rate 10/s, burst 20, actions 10, tool calls 4, agent turns 4 |
| 3 tool-call flood | `{"label":"A-agent","url_env":"BURST_A_AGENT","rate":1,"count":60,"after":15,"concurrency":20,"payload":{"calls":10}}` | tool calls 4 |
| 4 Redis down | scenario 1's streams for ~6 min; mid-run, point the executors at an unreachable Redis for 5 min (see below) | as scenario 1 |
| 5 wide fan-out | `{"label":"A-scatter","url_env":"BURST_A_SCATTER","rate":0,"count":1,"after":20,"payload":{"n":500,"secs":2}}` | actions 10 |

Notes:

- `insert_rows` rejects batches over 1,000 rows before taking the lock.
- Payloads over `TRACECAT__RESULT_EXTERNALIZATION_THRESHOLD_BYTES` go to blob
  storage and don't load the worker. The code default is 131,072 bytes and
  `.env.example` sets 128,000. Read the deployment's value first and keep
  `pad_bytes` under it, or the payloads are externalized and scenario 2 stops
  loading the worker. 120,000 bytes was under staging's value.
- Scenario 4 is staging only and needs an approval first. Make Redis
  unreachable for the executor only by keeping its real cache host and changing only the port to one
  the cache's security group drops, so connects time out like a network
  outage. With `REDIS_AUTH_MODE=aws_iam` the URL must stay `rediss://` and the
  host must end in `.cache.amazonaws.com`, or the client rejects it before
  connecting (that tests a client error, not an outage). Swap the port in
  place without printing the URL, then roll back with
  `kubectl rollout undo --to-revision=<previous>`:

  ```bash
  K="kubectl --context tracecat-staging -n tracecat"
  url=$($K get deploy tracecat-executor -o json \
    | jq -r '.spec.template.spec.containers[0].env[] | select(.name=="REDIS_URL") | .value')
  new=${url/:6379/:6390}
  [ "$new" != "$url" ] || { echo "REDIS_URL has no :6379; nothing changed"; exit 1; }
  $K set env deploy/tracecat-executor "REDIS_URL=$new"
  ```
- While a run's lock pileup is up, count waiters with a read-only query on
  `pg_locks` (`locktype = 'advisory' AND NOT granted`).
