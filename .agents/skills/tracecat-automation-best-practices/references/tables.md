# Tables

- `core.table.create_table` creates columns but **not** unique indexes.
- Before using `insert_rows(..., upsert=True)`, create a unique index on the intended key
  column. Any workflow action that upserts requires the index to exist first.
- For event ingestion, create or update the event table before agent review. Prefer upsert
  keyed by the source's stable event ID, delivery ID, object ID, or a deterministic hash of
  source, event kind, timestamp, actor, and object. If no stable key exists, insert with a
  generated run ID, but still store the normalized payload before invoking the agent.
- Keep `insert_rows` batches at or below 1000 rows.
- Use table storage for durable workflow state, checkpoints between stages and subflows,
  per-item status, run IDs, timestamps, and bounded error samples.
- Keep opaque identifiers as strings, especially numeric-looking IDs that may exceed integer
  limits.
- Keep table names, column names, and case field names under 63 characters.

For MCP table operations, use `list_tables`, `get_table`, `create_column_index`,
`search_table_rows`, and `export_csv`. For workflow YAML, use `core.table.*` actions or
`tracecat_registry.core.table` imports inside run-python.
