# Run-Python and deterministic data work

Use `core.script.run_python` for deterministic data plumbing: transforming, normalizing,
redacting, loading/upserting table rows, forwarding data, batching, retries, joins, sorting,
simple dedupe guards, and per-item error handling. Consolidate this work into one clear action
rather than scattering it across many nodes.

## Loops and concurrency

- Prefer `core.script.run_python` loops over action-level `for_each`, even for bounded lists.
  `for_each` can create enough scheduled work to hurt the scheduler. Use it only when the user
  explicitly needs separate workflow action runs per item and accepts the concurrency tradeoff.
- Prefer `core.script.run_python` loops over `core.transform.scatter` / `core.transform.gather`.
  Scatter/gather has the same scheduler/concurrency risk; reserve it for explicit
  workflow-stream fan-out/fan-in requirements.
- Use `core.loop.start` / `core.loop.end` only when each iteration depends on prior action
  output.
- Use bounded async concurrency inside `core.script.run_python` for bulk HTTP or table
  operations.

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
