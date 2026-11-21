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
D=out/s5-on; mkdir -p $D
python burst.py sample --out $D/samples.jsonl --duration 600 &
python burst.py drive --out $D/requests.jsonl \
  --stream '{"label":"A-scatter","url_env":"BURST_A_SCATTER","rate":0,"count":1,"payload":{"n":500,"secs":2}}' \
  --stream '{"label":"B-quick","url_env":"BURST_B_QUICK","rate":2,"count":400,"concurrency":10}' \
  --stream '{"label":"B-agent","url_env":"BURST_B_AGENT","rate":0.4,"count":60,"payload":{"calls":4}}'
# after every run has closed:
python burst.py collect --out $D/runs.jsonl --since 2026-11-22T00:00:00Z --until 2026-11-22T00:20:00Z
python burst.py report $D --out $D/report.md
```

Stream labels start with the org (`A-…`, `B-…`); `report` groups by that
prefix. `collect` lists every DSL and agent-tool run started in the two
workspaces in the window, so keep other work out of them during a run.

## Scenarios (as run on staging, 2026-11-22)

Org B's steady load in every run: `B-quick` at 2/s (≥ 300 starts, 2 actions
each) and, once org B has a model credential, `B-agent` at 0.4/s with 4 tool
calls each (≥ 240 tool calls).

| # | Org A load | Limits on (org A) |
|---|---|---|
| 1 lock pileup | `burst_lock` `{"rows": R, "batches": N, "children": C}` (N concurrent upserts into one table, C subflows), plus `burst_agent` | `max_concurrent_actions` 10, DB cap 4 per executor process |
| 2 webhook burst | `burst_hold` flood, `rate` 0, `concurrency` 40, padded payload | `api_rate_limit` 10/s, `api_burst_capacity` 20 |
| 3 tool-call flood | `burst_agent` `{"calls": 8}` at a high rate | `max_concurrent_tool_calls` 4 |
| 4 Redis down | scenario 1 on, then executors pointed at an unreachable Redis | as scenario 1 |
| 5 wide fan-out | `burst_scatter` `{"n": 500, "secs": 2}` | `max_concurrent_actions` 10 |

The exact stream specs, scales and results are in the ENG-2613 report.
