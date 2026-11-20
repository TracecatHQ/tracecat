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
scores from its stored word list instead of its complete text: 1.0 when the
term itself is one of its complete words (a keyword inside a file name or
host), otherwise the 0.65 threshold. A field without a stored word list still
scores by strict word similarity against its complete text.

### Broad searches

Ranking costs a few microseconds per matching case, and an exact count reads
every match, so a keyword that matches most of a large workspace grows
without bound (about 9 s per relevance page at one million matches). A request
with `cap_broad_matches` bounds the counts for `search_mode` searches, and the
ranking for `full_text` searches in relevance order, in the common case (see
step 3 below). The case UI sets it, and API, workflow and MCP callers keep the
exact behavior unless they opt in. The code is in
`tracecat/cases/search_bounds.py`.

- Relevance order ranks only the 10,000 newest matches: the page adds
  `created_at >= cutoff`, where the cutoff is the creation time of the
  10,000th newest match. Cases created at that same instant are kept too.
  Older matches are left out of relevance order; column orders (newest first
  and the rest) still return every match.
- `POST /cases/search/aggregate` reads at most 10,001 matches. Beyond 10,000 it
  returns `total` 10,000, `capped` true and no per-status counts, which an
  exact count would have to read every match to give. The UI shows
  "10,000+ cases" and loaded counts per status group.
- `include_count`'s `total_estimate` stays exact.
- Column-ordered keyword pages (newest first and the rest) normally turn off
  plain index scans, so a rare keyword finds its matches through the text
  indexes and then sorts them instead of walking the date index. When the
  capped count shows more than 10,000 matches, the page may walk the date
  index instead: a page fills within its first rows (newest first at one
  million matches went from 1.4 s to 0.1 s). Planner settings are not part of
  the cursor fingerprint, so this can change from page to page safely.

The first page finds the cutoff in up to three steps:

1. Count at most 10,001 matches. With 10,000 or fewer there is no cutoff, and
   every match is ranked as before.
2. Read the 100,000 newest cases in date order, check each against the
   search, and take the 10,000th match. A broad keyword usually reaches it
   well before the end: one in every case needs 10,000 rows.
3. If the newest 100,000 cases hold fewer than 10,000 matches, find the
   matches through the text indexes and sort them by date. This step is not
   bounded: it reads every match, though at about a third of the cost of
   scoring them.

The first page's cursors carry the cutoff (a signed `anchor` in the shared
paginator), and later pages reuse it: every page ranks the matches created at
or after the 10,000th newest match at the time of the first page. Matches
created while paging can join later pages, as in any keyset pagination. A
cursor is rejected if its anchor differs, so later pages must send the flag
too. The request and cursor schemas reject unknown fields, so roll out the
API before the UI: an older API answers the flag with 422 and rejects
anchored cursors. Steps 1 and 3 turn
off plain index and sequential scans for their statement: with a LIMIT or
OFFSET, PostgreSQL favours plans that read the table in order and stop early,
and for a keyword it overestimates, those read the whole workspace. Bitmap
scans keep the text-index plan. Step 1 is the price of the bound: a search
pays about one capped count to learn whether it is broad. That is 40 ms for a
keyword with 8,000 matches, but for a search with many candidates and few
matches, such as two common words that rarely appear together, it is as much
as the page itself (about 0.2 s at one million cases).

At one million cases (one workspace, local PostgreSQL 16, warm cache):

| Search | Exact page / count | Capped page / count |
| --- | --- | --- |
| Keyword in every case | 8.6 s / 1.25 s | 0.26 s / 0.09 s |
| Keyword only inside file names (25%) | 2.5 s / 0.73 s | 0.40 s / 0.08 s |
| Keyword in 8% of cases | 0.97 s / 0.17 s | 0.81 s / 0.05 s |
| Two keywords, no common case | 0.21 s / 0.21 s | 0.41 s / 0.21 s |
| Rare keyword | 36 ms / 13 ms | 34 ms / 12 ms |

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
reach 0.65 similarity with a typo unit of at most 32 letters. A source with a
remaining word over 1,000 bytes, which could exceed an index entry, stores a
NULL list, and search splits its complete text per row as before. The
vocabulary has a trigram GIN index and no unique constraint, so
concurrent case writes never wait on each other's new words; a race may store
a duplicate, which search ignores. It copies case text, so it carries the
standard workspace row-level security policy. The vocabulary is insert-only. Words of
deleted or edited cases remain until their workspace is deleted, and cannot
produce matches because each case's own list is always checked.

A fourth migration indexes sources over 8,192 bytes, which the first three
leave with a NULL vector and word list. Without it, every full-text search
re-parsed each such row in SQL (about 1.7 ms per row at 9 KB), whatever the
keyword, so a few thousand long descriptions made every search take seconds.
A third trigger, `trg_case_search_words_oversized`, runs after the other two
(PostgreSQL fires same-event BEFORE triggers in name order) and, for a source
over the budget only:

- stores its distinct native lexemes without positions in `summary_lexemes` or
  `description_lexemes`, using exactly the token rules of the per-row matcher,
  so an indexed word match gives the same answer;
- stores its complete word list in `summary_words` or `description_words`, and
  adds those words to the vocabulary.

Word clauses then match through the lexeme GIN indexes. A phrase uses them for
candidates and is checked against the complete source per row. Partial
trigram indexes over the complete lowercase text of NULL-vector sources
(`ix_case_*_trgm_fallback`) let literal and legacy substring search find long
sources without reading each one in full. A source keeps the
per-row check only if it has neither a vector nor lexemes: an unsafe token
below the budget, or more than 1 MB of distinct word text, which
`array_to_tsvector` would otherwise store as a silently corrupt vector. The
trigger inlines its SQL instead of calling helper functions, so case writes
need no extra `EXECUTE` grant. Writing a 10 KB source costs about 2 ms more;
other writes are unaffected.

The immutable safe-vector helper returns a NULL sentinel above 8,192 source
bytes or for conversion-unsafe tokens. A stored NULL keeps that meaning. Token
safety is checked before native conversion. Its defensive exception boundary is
marked parallel unsafe because PostgreSQL exception handling starts a
subtransaction. It is not safe to execute that boundary in a parallel
operation.

Safe ordinary positive phrase matches use the native vector fast path. A
negative phrase with all required words present is rechecked against the full
native positional stream to recover positions beyond the 256-occurrence cap.
Sentinel documents without lexemes use the same complete stream for words and
phrases.
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
and each statement gets its own budget. Two planner settings apply to the
same transaction, including the legacy substring search. JIT compilation is
off: search expressions are long enough to cross `jit_above_cost`, and
compiling them took about 30 ms per statement, several times the execution
time of a selective search. `plan_cache_mode` is `force_custom_plan`: whether
a keyword is rare or broad decides the right plan, and a cached generic plan
cannot see the keyword (a broad legacy search took 0.97 s instead of 0.31 s).
`TRACECAT__CASE_SEARCH_STATEMENT_TIMEOUT_MS` sets it (default 30 s). The budget
lets broad searches finish instead of failing, but it bounds each statement,
not the request: a search runs its page, match-detail and excerpt queries, and
the UI requests status counts in parallel. When the client aborts a
superseded search, the API cancels it. `CancelOnDisconnectMiddleware` cancels
the request handler for `POST .../cases/search` and `.../cases/search/aggregate`,
and the routes call `pg_cancel_backend` for the request's backend before the
cancellation unwinds. The call uses its own unpooled connection, so it works
when the pool is exhausted, and it repeats every second until the search ends,
since a cancel that lands between two statements has no effect. When it
succeeds the statement stops in PostgreSQL and the connection returns to the
pool; if it cannot connect, the statement budget is the fallback. The UI also
skips its 120 s auto-refresh while a search is applied. Workflow,
API and MCP callers that do not disconnect still run each statement to its
budget, and many concurrent slow searches can exhaust the API's connection
pool; lower the budget if that happens. A timeout returns the shared
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

Case writes run the word-list trigger as the writing role. Creating a case, or
changing its summary or description, calls the word-list helper and may add
vocabulary words, so that role also needs `SELECT` and `INSERT` on
`public.case_search_word`. Without them those writes fail rather than skip the
vocabulary; status and other field updates are unaffected.

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
