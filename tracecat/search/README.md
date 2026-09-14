# Semantic search storage contracts

ENG-1816 defines persistence and shared types. It does not register a route or
workflow action, call a provider, schedule work, or alter existing table writes.
The next PRs supply those pieces. Provider setup and collection selection must
validate permissions and source/credential identities before calling storage.

## Data ownership

- `SearchWorkspaceState` holds the current configuration version and starts
  disabled. Saving a configuration does not enable search.
- `SearchEmbeddingConfig` stores immutable provider/model semantics, dimensions,
  limits and a workspace credential reference. Provider code owns validating that
  binding; do not store credentials here. Credential rotation can resolve new
  secret values under the same binding without changing vector semantics.
- `SearchCollection` identifies a table by UUID and pins selected column UUIDs,
  chunker settings, configuration version and generation. A generation changes
  when selection/labels/chunker semantics change; it invalidates old chunks
  immediately, before backfill visits individual rows.
- `SearchDocument` identifies a source row within its collection. Desired
  revision advances on source changes. Build revision records current work;
  indexed revision is non-null only when that complete revision is published.
- `SearchChunk` holds a slice reference, labeled-input hash and optional vector.
  Offsets are zero-based Unicode character offsets, start inclusive/end exclusive,
  into original source text. Each chunk belongs to one selected column. The
  vector has no fixed database dimension; a composite configuration FK and
  vector checks bind it to dimensions in 1–3,072. Zero and non-finite vectors
  are invalid. No approximate vector index is added.

Composite foreign keys bind derived parents and children to the same tenant.
There are intentionally no foreign keys from these tables to source workspaces,
user-table definitions or physical rows: ordinary deletion must not cascade
through arbitrarily many chunks. RLS verifies the live workspace and both tenant
IDs. Service reads also verify the workspace when RLS is disabled. The query
adapter MUST additionally join the live physical source row, including when
serving a cached cursor; `eligible_chunks()` only joins table metadata.

## Transactions and lock order

Use short READ COMMITTED transactions; callers own commit/rollback. Roll back
on any storage exception. Serializable callers must retry serialization errors.
Never keep a transaction or connection open during provider calls.
`SearchStorage.with_session(scope=...)` creates and closes a session without
committing it. Pass `session=...` to reuse a caller-owned session without closing
it. An explicit trusted scope is required in both cases; it does not grant RLS
permissions or replace the caller's authentication context.

Every participating writer, backfill transaction and worker follows this order:

1. `SearchStorage.lock_scope()`: transaction advisory lock keyed by organization
   and workspace, then a key-share lock on the live workspace row.
2. Configuration/collection state under that lock.
3. Physical source row locks, in UUID order when more than one row is involved.
4. Document/chunk work through storage primitives.

The workspace lock exists before any collection row, so concurrent enablement
cannot bypass write bookkeeping by racing creation. It deliberately serializes
short search bookkeeping transactions within a workspace for the MVP. Source
writers must acquire it before locking source rows; calling storage after source
locks reverses the order and can deadlock. All participants must adopt this
order before the feature is enabled. Cross-workspace operations lock workspaces
in UUID order. No network calls occur inside these transactions.

## Work lifecycle

`touch_document()` shares the source writer's transaction and invalidates the
previous revision. With `backfill=True`, an already-known document is left
untouched so backfill cannot overwrite newer source bookkeeping. A new collection
generation resets work when the document is next claimed. Source deletion marks
a document tombstone; table deletion calls `tombstone_collection()` in O(1).

`claim()` uses database time and returns a fencing token. Active leases exclude
other claims. A retry after lease expiry gets a higher fence and retains durable
progress for the same revision. Each write rechecks tenant, current configuration,
generation, desired/build revision, fence, lease and tombstone under the lock.
An old provider response cannot publish after edits, reconfiguration or deletion.

`checkpoint()` accepts at most 32 contiguous chunk manifests and a before/after
cursor. Manifest creation and cursor advancement commit together. An identical
checkpoint retry is idempotent; conflicting manifests fail. The chunker owns
Unicode coverage and token-budget correctness. `write_embeddings()` accepts a
bounded response with matching hashes/configuration/dimensions. Completed chunks
are not overwritten by duplicate responses.

`publish()` requires enumeration completion and a database query proving that
all expected ordinals exist and are embedded. It does not trust a completed
counter. No chunk from a partly embedded row becomes eligible. Empty documents
publish as `empty` with zero chunks. Publication retries are idempotent.

`fail()` stores only a typed safe error code. With a retry delay it becomes
claimable after that delay; without one it stays failed until `retry_document()`.
Provider-specific error text, queries, source text and vectors must not be logged.
`checkpoint_backfill()` advances the source keyset cursor transactionally.
`status()` reports counts; callers must treat an incomplete backfill as partial.

## Pause, rollback and cleanup

Paused workspaces continue source bookkeeping but block claims and search.
Resuming active preserves pending edits. Rolling back to application code without
bookkeeping is different: mark `reindex_required` before rollback. This advances
the current version so old chunks cannot become eligible even if state is later
changed. Recovery saves a new validated configuration, rebuilds collection
selections against that version, then sets the workspace active so workers can
backfill. Activation from `reindex_required` fails until a new configuration
exists. Collections still bound to the old version remain ineligible. Wait for
backfill before relying on readiness.

`cleanup_chunks()` deletes at most 1,000 stale/tombstoned chunks per transaction.
`cleanup_orphans()` is for a trusted maintenance session with RLS bypass and
removes bounded batches after workspace/table deletion, children first. It is
not exposed to request callers. No source deletion waits for cleanup.

## Deployment and downstream boundaries

The normal additive migration requires pgvector >= 0.8.0 in public. It checks
availability and never attempts privileged installation. Apply ENG-1815's
provisioning first, even if search is disabled. Existing source tables are
unchanged and old application code can operate on the expanded schema.
Downgrade drops derived search data, retains the extension and source data, and
requires workers to be stopped. Provider, lifecycle and worker PRs must not edit
this migration after publication.

Shared action/page DTOs live in `schemas.py`: default limit 10, maximum 100,
strict readiness by default, row references with at most 1,000-character
excerpts, cursor/has-more and capped-window metadata. Query token limits,
permissions, source joins, ranking, Redis cursor binding and provider execution
belong to the later search and provider PRs. Temporal scheduling, pause controls,
configuration reconciliation and cleanup dispatch belong to the worker/lifecycle
PRs. Search settings and runtime state are not exported through schema sync.
