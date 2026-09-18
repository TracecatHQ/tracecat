# Table semantic-search lifecycle (ENG-1819)

All source hooks use `TableSearchService` in the caller's PostgreSQL transaction.
They acquire the storage workspace advisory lock before source row/DDL locks,
including while there is no collection. They never resolve credentials, prepare
chunks, or call a provider. `record_rows` delegates to the shared storage
`touch_documents` primitive, which coalesces work by row, increments the desired
revision and fence, and clears publication. Single-document and backfill writes
use the same primitive. Deletions remain tracked while selection is disabled,
so re-enabling cannot leave pending work for missing rows. A bookkeeping database
error fails the source transaction. There is no reliance on after-commit callbacks.

## Public interface

All routes live under `/tables/{table_id}/search` with the existing workspace
role dependency. Reading requires `table:read`; selection/retry require
`table:update`. Provider availability additionally uses `workspace:read`.

- `GET /`: generation, stable selected column IDs, display state and row counts.
  It checks current provider availability separately from source transactions.
- `PATCH /selection`: `{column_id, enabled, expected_generation}`. Zero means
  no existing collection. A stale generation returns 409
  `CONFIGURATION_CHANGED`; replaying the current desired selection is a no-op.
- `GET /documents?generation=...&cursor=...&limit=20`: document/row IDs,
  revision, safe state/error and chunk progress. Cursor is the last document UUID;
  maximum page size is 100. Generation mismatch returns 409. A chunk sample reads
  at most 1,001 states per document and explicitly marks capped counts. The final
  expected total is unknown until enumeration completes. This is a live progress
  view, not a stable ranked result cursor.
- `POST /retry`: `{expected_generation, document_ids}` (1–100 IDs). Retries
  current-generation failed documents belonging to this table; returns 204.

Use the generated client. There is no provider configuration write endpoint,
new unique-index flag, or search-results UI in this change.

## Worker handoff

A new collection can have `config_version = NULL`: the column selection and
backfill marker are durable even before a provider exists. Existing configuration
bindings retain their meaning. NULL never passes worker/search eligibility.
The follow-up migration makes only this reference nullable; it does not modify
sibling migrations or persist a fake provider configuration.

The dispatcher must discover enabled, non-deleted collections with NULL or stale
configuration versions, as well as pending documents and unfinished backfills.
Resolve the provider outside the source transaction. If unavailable, leave the
selection and work intact. Otherwise use the shared `configure_collection`
primitive under the scope lock to bind the actual configuration and pinned
chunker settings, increment the generation, and restart backfill. Selection
changes preserve an existing binding rather than attaching a new model to old
chunker settings. Respect workspace pause/reindex-required state.

`TableSearchSource(session, trusted_scope)` implements the chunker's
`SourceReader.read_slice(identity, column_id, start, limit)`. It reads at most
`limit + 1` original Unicode characters in SQL (limit <= 65,536), joins to the
expected document revision, and checks collection generation/configuration under
the shared lock. A mismatched/missing/deleted source raises a typed error. Close
this transaction before any provider call.

`scan_rows(collection_id, generation, after, limit)` returns at most 1,000 row
IDs/revisions, plus `has_more`, without loading source text. In the same
transaction, call `touch_document(backfill=True)` for discovered IDs and
`checkpoint_backfill` for the page. Use the returned document's revision for
builds: the storage primitive may advance it when requeuing an older generation.
Concurrent inserts behind the scan cursor are captured by normal write hooks.

The chunker's complete versioned checkpoint must be preserved by the worker;
this ticket does not change its format or the sibling worker's persistence.

## Writers and schema sync

Covered: base and committing services, single/batch CRUD and upserts (including
NULL-preserving batch upserts), CSVImporter and `_insert_import_chunk`, outer
transactions and savepoints. Managed-table writes through `TableEditorService`
delegate to the base service; the separate custom-fields schema retains its
existing direct SQL behavior. Selected column rename/type/deletion rebuilds or
disables the collection; table rename retains its UUID. Table deletion tombstones
the collection without synchronous chunk cleanup.

Workspace sync already calls the base service, so it receives the same hooks
without exporting local selection, embeddings, credentials, or runtime state.
Its existing policy of rejecting removed Git columns remains unchanged.

## Deployment and validation

Enable semantic search only after all compatible writers are deployed. Old
writers do not maintain revisions. A rollback to those writers requires disabling
search and rebuilding before re-enabling. Migration downgrade refuses NULL
bindings rather than deleting saved selections; bind or deliberately remove
those selections before downgrading.

Focused live tests are in `tests/integration/test_table_search_lifecycle.py` and
`tests/migration/test_table_search_configuration_migration.py`. Existing table
and storage suites verify compatibility. Tests use synthetic data in isolated
local PostgreSQL databases. No provider calls or Temporal implementation are
introduced; integrated worker execution belongs to ENG-1820/ENG-1823.
