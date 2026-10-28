# Case lexical search

Case search can find required words across summary and description, keep quoted
phrases within one field, tolerate bounded word typos, and return relevance
ordering and plain-text match excerpts. It uses PostgreSQL and pg_trgm; it does
not require an embedding provider or an additional search service.

## Inputs and compatibility

Public and internal `POST /cases/search` take the canonical `CaseSearchRequest`.
Their status-count endpoint, `POST /cases/search/aggregate`, takes the same
matching criteria. Public/internal search GET adapters also accept the lexical
options. The registry SDK, workflow action, and MCP tool forward these options.
General case aggregation remains structured-filter-only.

Set `search_mode` to `full_text` or `literal` and provide `search_term`.
`search_columns` optionally selects `summary`, `description`, or both; omitted
columns search both. Explicit empty, duplicate, or unsupported selections fail
validation. Selected columns require an explicit mode. Workspace and
structured filters are applied before pagination and counting.

- `full_text` consumes the shared lexical parser and fixed PostgreSQL
  `pg_catalog.simple` configuration. Every unquoted unit is required; separate
  units may match different fields. A quoted phrase must match one field.
  Punctuation, compound words, URLs, Unicode, and operator-looking text use the
  native parser. `OR`, `-word`, and `field:value` do not introduce operators.
- `literal` matches the complete case-insensitive character substring, preserving
  leading/trailing spaces. Backslash, `%`, and `_` are escaped. At least three
  characters after trimming are required.
- Omitted mode keeps shipped case search behavior: one escaped substring with
  the existing identifier alternatives and the existing default column order.
  Existing workflow and API callers do not silently acquire word semantics.

The shared bounds are 1,000 Unicode characters and 32 whitespace-separated
full-text terms, including words inside phrases. Empty/punctuation-only
full-text units fail validation. A case-specific native byte policy requires
query tokens to contain fewer than 2,047 raw and normalized bytes. This prevents
native conversion failures, including Unicode lowercase expansion; rejected
queries suggest literal mode. Literal mode retains the shared character bound.

A complete prefixed case ID or canonical hyphenated UUID matches exact identity
and textual mentions in full-text mode. Bare numbers remain ordinary word input.
Explicit `short_id` and `case_id` remain strict constraints. Literal mode matches
textual occurrences, preserving input characters through the same applied-query,
debounce and cancellation path as rows and counts.

The case UI opts into `promote_exact_match`: one search request returns the
workspace-authorized exact case before ordinary matches, even outside the view
filters. The result is labeled when it falls outside those filters. Date, status
and other controls remain active for ordinary results; there is no scope selector.
Filtered totals and status counts retain their ordinary-population meaning. The
exact priority participates in the database cursor ordering, so one-item pages,
backward navigation and deletion of the promoted row remain safe.

## Ranking and returned details

Full-text defaults to descending relevance, then descending case UUID. Explicit
column ordering remains authoritative for ordinary matches, including assignee/tag ordering and
opaque backward cursors. Literal mode uses column ordering. The UI remembers
explicit column-sort intent even when the user chooses the default column.

Integer score tiers prefer exact identifiers, whole-summary matches, contiguous
text, all-exact units, then fuzzy matches. Matching summary/description
contributions use 70/30 weights. Native cover-density rank is bounded within a
tier; complete-source fallback matches receive a bounded exact contribution.
Scores are ordering values rather than probabilities or corpus-wide BM25.

At most two ASCII words of 5–32 letters receive typo expansion. Identifier,
IP, hash, phrase, and punctuation-bearing query units do not receive it. A typo
unit matches a field when one complete word of it (a run of letters and digits)
has pg_trgm strict word similarity of at least 0.65 with the unit. Search first
finds the workspace vocabulary words within that distance once per query, then
keeps cases whose stored word list contains one of them. A typo-matched field
still scores by strict word similarity against its complete text. There is no
capped initial candidate window.

The selected page retains narrow projections and independent detail flags.
Match attribution and excerpts are fetched only for surviving returned IDs,
excluding lookahead rows. `search_match` carries score, matched columns, and at
most two excerpts of at most 240 characters. Excerpts are untrusted plain text;
the UI renders them as React text. `include_count=false` still skips the
population count, and status counts use exactly the same text predicate.

## Storage and native limits

Historically migrated case columns are unbounded VARCHAR, even though current
SQLAlchemy metadata declares narrower lengths. Whole-document tsvector
expression indexes would therefore reject legal old-version writes beyond the
native vector-size limit. Native vectors also retain only 256 positions per
lexeme and saturate positions at 16,383, which can lose a real late phrase even
in a short repeated document.

The additive migration installs two localized native helpers and six indexes:
two guarded GIN vectors, two guarded lower-text trigram GIN indexes, and two
small partial workspace/ID indexes for fallback documents. It does not narrow
columns, truncate source text, add a chunk table, or require application dual
writes. Existing and new INSERT/UPDATE/DELETE operations maintain these indexes.

A follow-up migration stores each field's safe vector in `summary_tsv` and
`description_tsv`. A `BEFORE INSERT OR UPDATE` trigger recomputes a vector only
when its source text changes, so old and new application writes stay current.
Search and ranking find candidates through GIN and NULL-fallback indexes on
these columns and read the stored vectors instead of recomputing the helper for
every candidate row, which was the dominant cost of broad searches. Excerpts
read the stored vectors for the already-selected page rows only. The columns
are table-only: they are not mapped ORM attributes, so loading or serializing a
case never fetches them.

The original expression indexes remain for the previous application version
until a later contract migration drops them. Until then, each change to a
summary or description computes the safe vector three times (old GIN
expression, old fallback predicate, and trigger) and maintains the four new
indexes as well as the old ones.

A third migration stores each field's distinct lowercase words in
`summary_words` and `description_words`, and every word seen in a workspace in
`case_search_word`. A second trigger maintains both on the same terms as the
vectors, and adds a moved case's words to its new workspace. Lists omit
digit-only words and words with more than 50 distinct trigrams: neither can
reach 0.65 similarity with a typo unit of at most 32 letters. A source over
8,192 bytes stores a NULL list, and search splits its complete text per row as
before. The vocabulary has a trigram GIN index and no unique constraint, so
concurrent case writes never wait on each other's new words; a race may store
a duplicate, which search ignores. The vocabulary is insert-only. Words of
deleted or edited cases remain until their workspace is deleted, and cannot
produce matches because each case's own list is always checked.

The immutable safe-vector helper returns a NULL sentinel above 8,192 source
bytes or for conversion-unsafe tokens. A stored NULL keeps that meaning. Token
safety is checked before native conversion. Its defensive exception boundary is
marked parallel unsafe because PostgreSQL exception handling starts a
subtransaction. It is not safe to execute that boundary in a parallel
operation.

Safe ordinary positive phrase matches use the native vector fast path. A
negative phrase with all required words present is rechecked against the full
native positional stream to recover positions beyond the 256-occurrence cap.
Sentinel documents use the same complete stream for words and phrases.
PostgreSQL's actual default parser, simple token mapping, and simple dictionary
supply tokens and normalization; there is no application tokenizer. Raw tokens
at or above 2,047 bytes are ignored without adding a position, matching the
supported PostgreSQL boundary. Omitted over-budget normalized lexemes retain
their attempted-position gap. A bounded sliding window preserves phrase
adjacency without whole-vector size or position saturation.

Fallback predicates retain their partial-index candidates and CASE evaluation
guards. The complete original source is still used for literal/fuzzy matching.
Native headline selection uses required words for a field whose phrase match
has already been proved, so large/position-saturated sources can return useful
late context. All database work runs within an owned transaction/savepoint,
and each statement gets its own budget.
`TRACECAT__CASE_SEARCH_STATEMENT_TIMEOUT_MS` sets it (default 30 s). The budget
lets broad searches finish instead of failing, but it bounds each statement,
not the request: a search runs its page, match-detail and excerpt queries, and
the UI requests status counts in parallel. The server does not cancel queries
when the client aborts a superseded search, so they run until they finish or
reach the budget. Many concurrent slow searches can therefore exhaust the API's
connection pool; lower the budget if that happens. A timeout returns the shared
`query_timeout` contract and rolls back the savepoint; it does not silently
return an incomplete result.

## Readiness and operation

The preceding shared migration enables pg_trgm when its role has permission;
restricted roles need administrator preinstallation. Operator provisioning and
normal-role readiness checks use the shared `scripts/postgres/pg-trgm.sql`
tooling. The case migration
only checks extension readiness and builds its own indexes concurrently. It
preflights existing function/index definitions and repairs only compatible
invalid concurrent indexes. It preserves unrelated name collisions and all
source data. Downgrade removes its own indexes/functions and retains pg_trgm.

If migrations and the API use different database roles, verify the API role
after the case migration has run. The shared pg_trgm preflight checks extension
functions; it cannot check case helpers before they exist. A migration owner's
hardened default privileges can withhold helper execution from the API even
when the migration and index builds succeed.

Case writes run the word-list trigger as the writing role, so that role also
needs `SELECT` and `INSERT` on `public.case_search_word`; without them case
creation and edits fail rather than skip the vocabulary.

Run this read-only check with the actual API database credentials. Every row
must report `schema_usage` and `can_execute` as true:

```sql
BEGIN READ ONLY;
SELECT current_user, signature,
       has_schema_privilege('public', 'USAGE') AS schema_usage,
       coalesce(has_function_privilege(to_regprocedure(signature), 'EXECUTE'), false)
           AS can_execute
FROM (VALUES
    ('public.tracecat_case_safe_vector_v1(text)'),
    ('public.tracecat_case_text_matches_v1(text,text,boolean)'),
    ('public.tracecat_case_words_v1(text)')
) AS required(signature);
SELECT has_table_privilege('public.case_search_word', 'SELECT, INSERT')
    AS vocabulary_access;
COMMIT;
```

If `schema_usage` is false, the schema owner or an authorized administrator
restores it for the API role:

```sql
GRANT USAGE ON SCHEMA public TO synthetic_runtime_role;
```

When execution is missing, the function owner or an authorized administrator
grants only these helpers to the API role. Replace `synthetic_runtime_role` with
that role and rerun the read-only check:

```sql
GRANT EXECUTE ON FUNCTION public.tracecat_case_safe_vector_v1(text)
    TO synthetic_runtime_role;
GRANT EXECUTE ON FUNCTION public.tracecat_case_text_matches_v1(text,text,boolean)
    TO synthetic_runtime_role;
GRANT EXECUTE ON FUNCTION public.tracecat_case_words_v1(text)
    TO synthetic_runtime_role;
GRANT SELECT, INSERT ON public.case_search_word TO synthetic_runtime_role;
```

The migration does not infer application roles or restore `PUBLIC` execution.
These invoker functions retain the caller's existing database privileges.

Runtime checks include the actual strict-word function/operator permissions,
localized helper signatures/bodies, fixed simple configuration, the two trigram
index definitions, the stored-vector trigger, its function attributes and body,
the four stored-vector index definitions/readiness, and the same checks for the
word-list helper, trigger, vocabulary table, and its six indexes. Missing
prerequisites produce shared typed availability errors rather than a silent
sequential-search fallback. The existing signed paginator fingerprints query
values, ordering, and lexical mode/columns/configuration generation;
incompatible continuations are rejected.

Synthetic benchmarks cover four workspaces, selective/broad queries, exact/
phrase/fuzzy judgments, write cost, and complete-source oversized documents.
`tests/integration/test_case_search_scale_benchmark.py` measures page and
count latency at 100,000 cases per workspace. Broad typo-matched words remain
the slowest path because each non-exact row computes trigram similarity against
its full source text. These local warm measurements establish correctness and
tradeoffs, not a production RDS latency promise. BM25 remains a separate
prototype if representative relevance judgments show a ranking gap.
