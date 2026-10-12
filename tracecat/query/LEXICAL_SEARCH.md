# Shared lexical search semantics

This contract belongs to `query/lexical.py` and `query/lexical_types.py`. Resource
adapters supply authorized fields, SQL, indexes, ranking, and pagination. These
modules do not execute queries or change existing API defaults.

## Modes, scope, and ordering

Explicit `full_text` finds words; explicit `literal` finds a case-insensitive
character substring. `SearchScope` is `all_columns` or a nonempty tuple of
selected canonical column names. Tables permit TEXT, JSONB, SELECT, and
MULTI_SELECT; case adapters define their own allowed text fields. Resolve
aliases and authorization before `resolve_search_columns`. Missing or
nonsearchable selected columns, duplicate selections, and zero searchable
columns are validation errors, never an unfiltered result.

`SearchOrder` is `relevance` or `ColumnSearchOrder(column, direction)`. Relevance
is descending, full-text only, and must append a stable resource ID tie-breaker.
Column ordering must resolve a sortable column and append the same tie-breaker
in the requested direction. Cursor context binds query, mode, scope, ordering,
and index/configuration generation. Ranking weights belong to each resource.

## Full-text units and values

The parser preserves case, punctuation, and Unicode in whitespace-separated
units. Double quotes delimit a phrase; quotes must occur at unit boundaries.
`\"` and `\\` decode to a quote and backslash inside or outside phrases. Other
backslashes, including a trailing backslash, remain literal. Unclosed quotes,
empty phrases, and units with no Unicode letter or number are rejected. The
letter/number guard only screens trivial input; PostgreSQL remains authoritative.

Every unit is required. Separate unquoted units may match different selected
columns. Each unquoted unit is parsed with
`plainto_tsquery('pg_catalog.simple', unit.text)`. A phrase uses
`phraseto_tsquery('pg_catalog.simple', unit.text)` and must match one source text
value, JSON key, or JSON scalar. Do not evaluate phrases against concatenated
columns, keys plus values, arrays, or unrelated JSON scalars. Unquoted units
containing multiple PostgreSQL lexemes require all those lexemes. Reject any
unit whose PostgreSQL query has zero nodes instead of silently dropping it.
These are application rules built on [PostgreSQL query controls](https://www.postgresql.org/docs/16/textsearch-controls.html).

There is no operator language: `OR`, `AND`, `NOT`, `-word`, `word:*`, and
`field:value` are plain required text. They never introduce Boolean logic,
exclusions, prefixes, or field selectors. Standalone punctuation such as `|`
fails the meaningful-character guard. Structured predicates use the existing
`query.filters` model/compiler separately.

Index and query text use the same explicit `pg_catalog.simple` configuration;
never depend on a session default or substitute English stemming. Preserve
accents and avoid application-side normalization. PostgreSQL's [parser](https://www.postgresql.org/docs/16/textsearch-parsers.html)
controls Unicode boundaries using the database locale. Punctuation can split
identifiers; hyphenated words can produce overlapping tokens. Domains, IP
addresses, paths, and hashes follow PostgreSQL parsing rather than guaranteed
whole-identifier matching. Use literal mode when a character fragment matters.
The [simple dictionary](https://www.postgresql.org/docs/16/textsearch-dictionaries.html#TEXTSEARCH-SIMPLE-DICTIONARY)
folds case without stemming; do not alter the built-in configuration or add
custom stop words behind this contract.

Recursively index nested JSONB object keys and non-null scalar values, including
array elements. Treat each key/scalar as a separate source value, not serialized
JSON syntax or a synthesized path. Strings use their decoded text; numbers and
booleans use PostgreSQL's text representation; null contributes nothing. SELECT
uses its stored text value, and MULTI_SELECT treats each selected string as a
separate value. A phrase cannot span two selected values. Objects and arrays
are containers, not searchable scalars.

## Literal and omitted-mode compatibility

`literal_search_pattern` returns a bound ILIKE contains pattern with backslash,
`%`, and `_` escaped. Execute with explicit backslash `ESCAPE`. Preserve the
input's spaces and JSON serialization: JSONB and MULTI_SELECT literal searches
use their current PostgreSQL text casts, including keys and structural syntax.

Existing table workflow/API inputs with omitted mode keep their original path:
one complete substring across any eligible column, serialized JSON casts,
unescaped `%` (any sequence) and `_` (one character), and existing backslash
LIKE escape behavior. They keep requested column ordering, defaulting to
`created_at desc` with row ID as tie-breaker; quotes and operator words have no
special meaning. Only those callers select `LEGACY_TABLE_WILDCARDS`. Empty
input still means no search; short or punctuation-only legacy inputs remain
accepted. The existing no-searchable-column path currently logs and leaves the
query unfiltered; adapters adopting this contract must fail explicitly instead.
That existing defect is recorded here, not changed by this boundary-only PR.

Case literal matching always escapes wildcards, following ENG-2012, and never
selects the legacy table policy. New explicit literal search requires at least
three characters after trimming for validation; actual matching preserves the
original input. Escaped `%`/`_` are characters, not broad wildcard queries.
Legacy case API validation remains its adapter's compatibility responsibility.

## Bounds, failures, and metadata

Both parsers cap raw input at 1,000 Unicode characters and reject NUL. Full-text
caps whitespace terms at 32, including words inside quoted phrases and repeated
terms; this is a raw-input bound, not an imitation of PostgreSQL lexeme counting.
Explicit empty input fails validation. Literal mode permits punctuation when
it meets the length guard. Broad queries still use bounded pages and the
existing `query_execution_context` statement timeout; never remove safeguards
or fall back to an unfiltered scan when an index is unavailable.

`LexicalSearchError.code` distinguishes unavailable search, incomplete indexing,
and changed configuration. Query timeout uses the existing
`TracecatQueryTimeoutError` / `query_timeout`; do not add another timeout class.
Changed configurations invalidate continuations. Validation errors are separate
from availability errors; adapters own their API mapping.

`SearchMatchMetadata` travels beside resource data, never as keys inserted into
arbitrary row dictionaries. It permits a finite nonnegative relevance score,
at most 8 column names of at most 255 characters, and at most 3 plain-text
excerpts of at most 240 characters each. Metadata is partial when capped; score
is absent for literal matching. Render excerpts as untrusted text.

Lexical search needs no semantic provider, embedding-column selection,
semantic-search entitlement, or Redis retrieval window. It still enforces
ordinary resource permissions. Index provisioning, execution, endpoints, table
UI, typo tolerance, and semantic retrieval changes belong to consuming PRs.
