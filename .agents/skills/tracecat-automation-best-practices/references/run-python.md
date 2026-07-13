# Run-Python and deterministic data work

Use `core.script.run_python` for deterministic data plumbing: transforming, normalizing,
redacting, loading/upserting table rows, forwarding data, batching, retries, joins, sorting,
simple dedupe guards, and per-item error handling. Consolidate this work into one clear action
rather than scattering it across many nodes.

## Loops and concurrency

- Default to `core.transform.scatter` for workflow-level loops: scatter alerts into
  `core.cases.create_case`, run `ai.agent` or `ai.preset_agent` per item, branch enrichment,
  or call `core.workflow.execute`. Add `core.transform.gather` only when downstream steps need
  combined results; omit gather otherwise.
- Use best judgment: choose `core.script.run_python` for data-heavy in-process loops such as
  transforms, batching, joins, dedupe, sorting, grouping, chunked table writes, batch table
  uploads, bounded async HTTP loops, and per-item error handling where separate workflow
  streams are unnecessary.
- Avoid action-level `for_each` by default. Use it only for known, bounded lists when the user
  explicitly needs separate workflow action runs per item and accepts the scheduler/concurrency
  tradeoff.
- Use `core.loop.start` / `core.loop.end` only when each iteration depends on prior action
  output.
- Use bounded async concurrency inside `core.script.run_python` for bulk HTTP or table
  operations.
- Avoid `scatter -> gather` for high-fanout table/case writes. Scatter is useful, but >10
  concurrent DB-backed branches can exhaust Postgres connection slots. Scatter's optional
  `interval` can stagger stream creation, but it is not a database batch/throttle primitive; do
  not rely on retries to fix connection-starvation fanout. Prefer one native
  `core.table.insert_rows` action when the input is already row-shaped (up to 1000 rows per
  batch), or batch writes inside `core.script.run_python` when you need shaping, chunking, or
  mixed side effects.

## Bulk table writes

If rows are already shaped, do not scatter an insert action per item. Insert them once:

```yaml
- ref: insert_domain_data
  action: core.table.insert_rows
  args:
    table: qradar_domains
    rows_data: ${{ ACTIONS.shape_domain_rows.result }}
    upsert: false
```

Use `core.script.run_python` when the rows need transformation or bounded chunking:

```python
from tracecat_registry.core.table import insert_rows

BATCH_SIZE = 1000

async def main(domain_items: list[dict]) -> dict:
    inserted = 0
    errors = []

    for start in range(0, len(domain_items), BATCH_SIZE):
        batch = domain_items[start:start + BATCH_SIZE]
        rows = [
            {
                "domain_id": item["id"],
                "name": item.get("name"),
                "payload": item,
            }
            for item in batch
        ]
        try:
            inserted += await insert_rows(
                table="qradar_domains",
                rows_data=rows,
                upsert=False,
            )
        except Exception as exc:
            errors.append({"batch_start": start, "error": str(exc)})
            if len(errors) >= 5:
                break

    return {"input_count": len(domain_items), "inserted": inserted, "errors": errors}
```

Set the run-python action's `allow_network: true` when imported helpers call Tracecat APIs:

```yaml
- ref: insert_domains_batched
  action: core.script.run_python
  args:
    script: <script above>
    inputs:
      domain_items: ${{ ACTIONS.retrieve_domains.result }}
    allow_network: true
```

## Imports

Use direct Tracecat imports inside `core.script.run_python` when consolidating table or HTTP
side effects reduces graph noise:

```python
from tracecat_registry.core.http import http_request, http_paginate
from tracecat_registry.core.table import create_table, insert_rows, lookup, update_row

async def main(rows):
    await create_table(
        name="automation_inventory",
        columns=[
            {"name": "external_id", "type": "TEXT"},
            {"name": "payload", "type": "JSONB"},
        ],
        raise_on_duplicate=False,
    )

    inserted = 0
    for start in range(0, len(rows), 1000):
        inserted += await insert_rows(
            table="automation_inventory",
            rows_data=rows[start:start + 1000],
            upsert=False,
        )
    return {"input_rows": len(rows), "inserted": inserted}
```

Use `async def main(...)`, `await` imported actions, pass named arguments, and chunk writes.
Catch expected per-item failures inside bulk actions and return counts plus a few examples
instead of failing the whole workflow. Keep outputs small: downstream-required rows, summary
counts, and bounded error samples — not full intermediate datasets.

## HTTP and pagination

- Prefer `core.http_request` for single generic API calls and `core.http_paginate` for cursor
  or next-URL APIs when the items and next request can be expressed clearly. Move the work
  into `core.script.run_python` when pagination, joins, retries, dedupe, or per-item error
  handling would make the graph noisy.
- Check `get_workflow_authoring_context` before using `core.http_paginate`; its pagination
  controls are lambda-string fields (`stop_condition`, `next_request`), not nested request
  objects.
- Keep paginated results bounded before storing or returning them.

## Inline expressions vs Python

Use inline expressions for small value shaping only: scalar fallbacks with `||`, simple
strings with `FN.concat`, CSV-style summaries with `FN.join`, and time windows with
`FN.to_isoformat(FN.now() - FN.hours(N))`. Use Python for anything that starts to look like
iteration or branching. Avoid empty-list fallbacks in expressions; use trigger defaults,
CSV/JSON strings, or Python-side defaults.

## Plain Python vs DuckDB

Default to plain Python for transforms that fit comfortably in memory. Reach for DuckDB only
for genuinely SQL-shaped work — real joins, multi-key aggregates, window functions, or large
row counts where vectorized execution is worth the overhead. Do not overstate DuckDB-removal
speedups without execution data; for low-hundreds-row transforms the win is usually simpler
dependencies, smaller scripts, and clearer graph review rather than a large runtime gain.
