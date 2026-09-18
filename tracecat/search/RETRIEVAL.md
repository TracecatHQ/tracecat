# Table semantic retrieval

`core.table.search(table, query, limit=10, cursor=None, allow_partial=False)`
uses selected TEXT columns. `search_rows` remains the literal-search action.
The SDK posts to `/internal/tables/{table_name}/rows/semantic-search` through
the existing executor action gateway. The API exposes the same internal route.

The service checks `table:read` and workspace ownership before looking up the
automatically selected embedding provider. Missing provider returns
`NOT_CONFIGURED`, including for partial results. The query is limited to 512
tokens (or the provider's smaller input/batch limit), using the pinned counter.
Provider failures propagate; there is no fallback to another provider.

Database transactions finish before provider I/O. The ranking transaction
rechecks workspace config, collection generation and readiness under the same
workspace lock as source mutations. Strict searches reject incomplete coverage.
Partial searches include only fully published current documents.

A materialized CTE filters tenant, collection, configuration, dimension,
generation, revision and live source rows before pgvector compares vectors.
Every eligible chunk receives an exact cosine similarity score. A window function
picks each row's best chunk before the top-100 row limit. Ordering is score
descending, then row UUID; tied chunks use stable column UUID then ordinal.
Ranking has a two-second statement timeout. Scores are similarities, not
probabilities or confidence estimates.

Redis stores at most 32 windows per workspace, each for five minutes. A window
contains at most 100 row/revision/chunk references and scores, never query or
source text. Its context hash binds actor identity/scopes, query/options,
collection generation and embedding version. Cursors authenticate window and
position with a random per-window key. Reads do not renew TTL; creating a 33rd
window evicts the oldest. Redis errors are failures, not pagination fallback.

Continuations authorize again, reconcile provider eligibility without embedding,
and revalidate all remaining references in one bounded SQL projection. Changed
or deleted rows are skipped, without refilling from new rows. Original text is
read with SQL substring: at most 1,000 Unicode characters per result. `start`
and `end` are half-open original-text offsets; `shortened` reports whether the
winning chunk extends beyond the excerpt. `capped` distinguishes a top-100
window from the entire matching population. Repeating a cursor is supported;
results may shrink as source rows change, but ordering and scores stay fixed.

Tests use a controlled local HTTP embedding server, live pgvector and Redis.
Real-provider semantic quality and rollout remain in ENG-1823.
