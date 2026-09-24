# Background semantic indexing

The DSL worker reconciles Temporal schedule `semantic-search-dispatch-v1` at
startup in a managed background task. Each reconciliation attempt has a
30-second timeout; failures log only their type and retry after 30 seconds.
Registration failure does not stop DSL polling, and shutdown cancels the task.
Every ten seconds it starts `SearchIndexDispatcher`; overlap is skipped.
The dispatcher keyset-scans 32 collections at a time, including disabled or
unconfigured collections, with up to six independent workspace jobs in flight.
Within each page, a free slot starts the next eligible job immediately; jobs for
the same workspace run one at a time without occupying other workspaces' slots.
It continues as new after 32 pages, preserving the cursor. A completed run can
start again on the next schedule tick: source writes require no Temporal call.

Each collection gets a bounded turn: clean at most 1,000 stale chunks and 100
childless deleted documents; reconcile its provider binding; discover 100 source
row IDs; then work on its oldest eligible document. A successful incomplete
batch yields its lease and moves behind other waiting documents. Large rows
never block the completion of a short row until their whole body is indexed.
The separate orphan sweep handles deleted tables/workspaces in bounded batches.

The full chunker checkpoint is stored directly in the existing `enumeration_cursor`
JSON column, with one copy of each position. It includes build identity,
configuration hash, overlap, covered offsets and pending prefix search. Earlier
nested checkpoints are validated and decoded into this canonical representation.
Older three-counter nonempty cursors
are rejected rather than silently inventing overlap state. Rebuild those
collections using `configure_collection` if upgrading an experimental worker.
The database column's type stays JSONB; there is no new migration.
Malformed checkpoints become durable `MANIFEST_CONFLICT` failures and stop
automatic retries. Rebuild the collection to replace its invalid checkpoints.

Preparation saves at most 32 manifests per turn. Newly prepared text and token
counts are reused directly. Missing chunks from a previous turn are reconstructed
and their hashes verified. Both paths respect the same provider input-count and
total-token limits; manifests outside the current provider budget stay saved for
the next turn. Tokenizer initialization and bounded tokenization/chunk computation
run in threads; database reads remain on their owning async event loop. The
database connection is closed before the network call. Publication requires
complete enumeration and every expected
chunk, guarded by the same revision, generation, config and fence as source
writes. A crash can repeat a billed call, but cannot publish an incomplete row.
Storage owns document eligibility, claim transitions, and the shared manifest
validation used by both explicit publication and the worker's finish-or-yield step.

## Limits and recovery

Redis leases atomically admit at most eight provider operations per deployment
and two per workspace. Background jobs use at most six and one respectively,
reserving foreground capacity; they retain the permit across database work too.
All keys share a Redis Cluster hash tag. Redis failure fails closed. The activity
has a 90-second deadline, its document/Redis leases last 120 seconds, and provider
calls have a 30-second deadline. A crashed worker's leases expire automatically.

Transient provider failures keep completed chunks and retry with exponential
backoff (honoring bounded Retry-After), at most five failed claims. Permanent or
exhausted errors require the table retry endpoint after the underlying issue is
fixed. Successful batches reset the failure budget. New edits invalidate prior
claims immediately. No eligible provider leaves durable work intact; the next
scan revisits it. Explicit workspace pause is respected. Reconfiguration binds a
new generation and restarts backfill; ordinary credential rotation does not.

Pause the Temporal schedule to stop new dispatches; startup reconciliation keeps
that operator pause. Resume it to rediscover pending work. Existing activities
finish their bounded turns. Workspace pause also prevents claims/publication.
Never delete source data or clear checkpoints to recover a transient outage.

## Observability and validation

OpenTelemetry instruments under `search.indexing.*` report batch outcomes,
prepared/embedded/cleaned chunk counts, known provider token usage, pending and
failed row samples, and queue wait since the selected row's last progress. The
`pending_rows_sample` and `failed_rows_sample` histograms count at most 100 live
documents per collection after the claim transaction releases its workspace lock.
They are bounded samples, not full-collection totals. Queue
wait measures scheduler delay, not a promised total time for a large document.
A cleanup count at its limit indicates more cleanup may remain. No source text,
query, vectors, credentials or raw provider errors enter workflow history or
these metrics. IDs and bounded progress summaries are the only payloads.

`tests/integration/test_search_indexing.py` exercises PostgreSQL, Redis, a
controlled local HTTP embedding endpoint and a real ephemeral Temporal server
(including startup reconciliation preserving pause). Storage and lifecycle
suites supply lease theft, stale publication, deletion and isolation coverage.
Real-provider relevance and production rollout remain ENG-1823's responsibility.
