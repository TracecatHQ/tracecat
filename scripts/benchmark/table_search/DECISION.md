# Storage decision: fenced, chunked per-value projection

Reject full-text expression indexes on unrestricted source values. Choose an
independent chunk projection for full-text search, with a separate bounded
serialized-value projection for literal search. The production storage PR must
implement the publication and correctness gates below before any caller can use
the index. This experiment does not approve its whitespace chunker for production.

## Why direct indexing is unsafe

PostgreSQL limits a `tsvector` to less than 1 MiB, lexemes to less than 2 KiB,
absolute positions to 16,383, and stored positions per lexeme to 256. These are
independent limits; a small vector can still lose phrase matches.
See [PostgreSQL 16 search limits](https://www.postgresql.org/docs/16/textsearch-limitations.html).

The live `results/*.json` safety probes reproduce three failures on source values
which PostgreSQL accepts as TEXT and nested JSONB:

- An approximately 5 MiB distinct-word value raises SQLSTATE `54000` when an
  expression GIN index is built. An already-built index also rejects its INSERT.
- A phrase after 20,000 words disappears despite a vector of only 552 bytes.
- After 300 repetitions, `echo beacon` does not match, even though the source
  ends with those adjacent words. Repetition-boundary and absolute-position
  probes record the exact transitions separately.

Clipping the source, discarding vector positions, or catching only index-build
errors would not fix these silent phrase omissions. Large-value trigram builds,
subsequent writes and tail matches also run independently in the safety probes;
trigram success does not establish full-text safety.

No TEXT/JSONB/SELECT/MULTI_SELECT per-cell maximum is enforced by the current table
write schemas. The ordinary CSV upload limit is a limit on the whole request,
not a source-cell invariant, and is configurable. Default probes reach 5 MiB;
the wide run additionally exercises 20 MiB via `--max-value-bytes`. There is no
claim that testing one finite value size proves all PostgreSQL-valid values safe.
The storage design must structurally separate source-write validity from search
projection validity, including after old-version writes.

Expression indexes impose computation on inserts and non-HOT updates, so their
write cost matters even when short values appear safe.
See [expression indexes](https://www.postgresql.org/docs/16/indexes-expressional.html).
Trigram patterns without extractable trigrams can require full-index scans.
See [pg_trgm index support](https://www.postgresql.org/docs/16/pgtrgm.html#PGTRGM-INDEX).

## Prototype and interpretation

The experiment compares independent per-column full-text and trigram stages with
both indexes on a per-value projection. Every stage consumes the merged shared
query parser and literal escaping policy. Separate terms can match different
columns; each phrase must match one TEXT value, JSON key/scalar or MULTI_SELECT
element. SELECT uses its stored value. JSON null is not a full-text scalar.
Literal mode indexes `jsonb::text` rather than joining its extracted values.

The experimental full-text chunker groups 128 whitespace words with 31 words of
overlap. Its bounded corpus passes the shared-contract fixtures, including a
phrase across a chunk boundary and a phrase forbidden across two JSON/array
values. Tail probes compare PostgreSQL full-value matching with chunk matching.
The experiment deliberately does not claim that whitespace words equal
PostgreSQL parser positions. A hyphenated identifier can emit multiple lexemes;
arbitrary punctuation and oversized single tokens defeat a naive word budget.
Its chunker also materializes each value's whitespace array. Neither behavior
is suitable as a general source-value implementation.

The literal prototype uses unmodified 8,192-character slices with 999-character
overlap. This covers every boundary-spanning literal permitted by the shared
1,000-character query limit, without normalizing whitespace or JSON punctuation.
UTF-8 bytes must also be bounded in production. The proposed full-text design
below uses parser positions and an exact phrase recheck, not a fixed whitespace
overlap assumption. Production ranking must be invariant under chunk layout.

## Physical layout and matching expressions

Names derive from trusted column IDs; labels never become SQL identifiers.
The storage PR owns quoting, organization/workspace authorization and RLS. A
shared definition must supply both index and query expressions; do not repeat
the expressions in unrelated services.

Direct comparison expressions in `schema.py` are:

```sql
to_tsvector('pg_catalog.simple'::regconfig, coalesce(text_column::text, ''))
jsonb_to_tsvector('pg_catalog.simple'::regconfig, json_column, '["all"]'::jsonb)
coalesce(column_name::text, '') -- literal; JSON uses PostgreSQL serialization
```

Chosen storage has a collection/generation header, a per-source-value manifest,
and bounded full-text/literal chunk rows. Each row carries organization,
workspace, table/collection, immutable column ID, row ID, source revision,
generation, representation version, value identity, chunk ordinal and source
span. JSON identity is a deterministic path plus key/value discriminator; array
identity includes its ordinal. Never infer identity from JSON iteration order
or text equality, and never concatenate unrelated values into a phrase window.

```sql
-- Full-text projection; already-bounded parser-equivalent chunk vectors.
CREATE INDEX ... ON lexical_chunks USING gin (vector);
-- Literal projection; unmodified serialized source spans.
CREATE INDEX ... ON literal_chunks USING gin (body public.gin_trgm_ops);
-- Point replacement, deduplication and scope fencing.
CREATE UNIQUE INDEX ... ON lexical_chunks
  (organization_id, workspace_id, collection_id, generation,
   row_id, source_revision, column_id, value_id, ordinal);
```

Full-text candidates use `vector @@ plainto_tsquery('pg_catalog.simple', :unit)`.
Phrases use the parser-equivalent phrase query and exact per-value positional
recheck described below. Literal candidates use `body ILIKE :escaped_pattern
ESCAPE '\'`; serialized source matching is rechecked where necessary. Every
predicate includes the authorized workspace, current generation, published
revision and selected column IDs. Use ordinary scope/revision B-trees as well as
GIN; no reliance on a globally selective term for workspace isolation.

For each full-text unit, OR candidates over the selected columns/values. AND
the resulting **row IDs** across units, then deduplicate before ranking and
pagination. Within a phrase, intersect postings by **the same value identity**.
Do not sum scores for overlapping copies of a token. Preserve unique absolute
source positions for ranking and excerpts, and define a score whose value does
not change when chunks split differently. Cursor order is score descending plus
stable row ID descending; include generation/query/representation identity in
the eventual opaque cursor. Literal ordering uses the requested column plus ID.

## Oversized values and phrase boundaries

The following are requirements for the storage PR, not capabilities of the
whitespace prototype:

1. Enumerate complete source values, including nested JSON keys/scalars. Use
   PostgreSQL `simple` parser/dictionary semantics. Record absolute parser
   positions and source offsets independently of local `tsvector` positions.
   Differential tests against PostgreSQL on values small enough for the oracle
   must cover hwords, identifiers, punctuation, Unicode, numeric/boolean JSON,
   and empty values. No language stemming or semantic tokenizer substitution.
2. Bound chunks by **both** parsed positions and UTF-8 bytes. Proposed initial
   caps are 128 emitted positions and 16 KiB of input per vector chunk, with
   additional checks that vectors stay below 64 KiB and contain no clamped or
   dropped positions. Limits reset per chunk, not per source value. An atomic
   token too large for the engine requires the explicit fallback below; splitting
   such a token into artificial searchable words changes full-text semantics.
3. Build phrase candidates from all required constituent lexemes within one
   value, even if they occur in different chunks. Recheck against absolute
   positions using a streaming window over adjacent chunk records. Derive the
   window span from the actual PostgreSQL phrase query, not the shared parser's
   whitespace-term count. It must cover expansion from compound tokens and
   arbitrary whitespace lengths. Bounded overlap may accelerate common phrases,
   but cannot be the only completeness mechanism. Repeated lexemes and phrases
   after position 16,383 must pass; unrelated source values must never share a
   window. Do not construct one oversized/clamped vector for the recheck.
4. Literal chunks preserve the whole PostgreSQL serialized value, including
   escape sequences and whitespace. Each next chunk repeats at least 999 source
   characters (or the current contract's maximum query length minus one).
   Maintain a Unicode-safe byte bound and do not cut bytes inside a character.
5. If safe representation cannot be completed within memory/time/storage limits,
   keep the source write valid and retain its revision in the durable backlog.
   Mark that row/column revision failed with a typed reason, leaving the
   collection unavailable/indexing rather than ready with missing tails. An
   exact streaming fallback may serve that revision only if it preserves the
   contract and obeys the query budget. Otherwise return the shared typed
   availability error. No success response with omitted source spans, partial
   chunks, or a stale published revision presented as current.

All source-size tests must exercise insert, update, upsert and import paths,
including an old application writer. The maximum supported source size follows
the source storage/request contract, never a new index-specific write limit.

## Update ownership and complete publication

Search computation runs independently of source transactions. Database-managed
change capture must increment/enqueue a durable row revision on insert/update,
upsert/import and tombstone/delete, including old-version writers. The source
transaction writes only bounded change metadata; it does not compute a vector
or call a provider. Enqueue failure must roll back the source transaction rather
than claim a ready current index with no change record. A malformed/oversized
search projection must not cause source-write failure.

A worker claims `(collection, generation, row, desired_revision, fence)`, reads a
consistent source revision, and builds all manifests/chunks in staging. Publish
in one transaction only if the desired revision, schema/query generation and
claim fence still match. Readers join the header's published revision; they
cannot observe staging chunks. A failure leaves a typed reason and retryable
backlog. A delete fences old builders and removes/tombstones all published
representations. Concurrent revisions cannot resurrect a deleted row.

Collection metadata includes selected immutable column IDs, explicit regconfig,
parser/representation versions, schema generation, query generation, desired
and published generation, build state, desired/published revision watermarks,
pending/failed counts, and safe error code. READY requires complete coverage of
the current source revisions across all selected columns. Configuration changes
invalidate old cursors and builders. Published generation switching is atomic;
old generations are reclaimed after readers no longer use them. Neither Redis
search windows nor semantic-search metadata determines lexical readiness.

## Proposed budgets for the storage PR

Validate on a declared, dedicated deployment-sized environment, at one million
rows, four workspaces, four selected columns, and a separately measured wide
schema. The current local warm-cache evidence is a design experiment, not proof
that these budgets hold in production.

| Workload/resource | Proposed acceptance budget |
| --- | --- |
| Selective full-text/literal returning 50 full rows | p95 <250 ms; p99 <500 ms; zero timeouts |
| Selective search under one sustained writer | Same p95; report writer throughput and queue lag |
| Broad full-text with relevance/phrase rechecks | p95 <1,000 ms; p99 <2,000 ms |
| Broad literal / zero-extractable-trigram pattern | p95 <2,000 ms; hard query timeout 5,000 ms; typed timeout when exceeded |
| Four-column projection + search indexes | <=10x source heap + TOAST bytes; record scoped B-tree cost separately |
| Large-value expansion | <=16 MiB projection/index bytes per MiB source value; bounded 64 MiB worker scratch per active value |
| Build / bulk projection throughput | >=10,000 source rows/s on the declared environment; million-row generation <=15 min |
| Source-write regression from change capture | p95 insert/update <=1.25x baseline; throughput >=80% baseline |
| Steady indexing backlog | p95 publication lag <10 s at the supported write rate; no unbounded backlog |

The synchronous prototype's projection write cost does **not** validate the
change-capture budget. Broad queries have their own budget because selectivity
and ranking can require inspecting many candidates even with GIN. Report failure
and the plan rather than widening these limits without a new decision. Storage
compression/grouping and worker backpressure may be necessary to meet the
expansion/build budgets while preserving value identity and complete publication.

The integrated-delivery issue must rerun this harness against the actual storage
adapter and production update ownership, add cold-cache/multiple-writer runs,
and enforce tokenizer-equivalence, phrase-window, revision-race, deletion and RLS
tests. Physical-design evidence does not enable application search.

## Recorded local results

Runs were collected on October 14, 2026, with PostgreSQL 16.15 (ARM64), UTF-8,
`en_US.utf8` collation/ctype, and `pg_trgm` 1.6. The shared OrbStack VM reports
18 vCPUs and 16,816,451,584 bytes RAM. The macOS host reports model Mac17,8,
18 logical CPUs and 51,539,607,552 bytes RAM. Docker storage is backed by the
local SSD; no container CPU/RAM limit was configured. Other development
containers and verification processes were active, so results include contention.
The PostgreSQL container image ID was
`sha256:ccc6e83d6e35e931dc7c5def2022729d5a6c370318d099181995567ff1fb4d6b`.

Engine defaults included shared_buffers=128 MiB, work_mem=4 MiB,
maintenance_work_mem=64 MiB, effective_cache_size=4 GiB, parallel workers per
gather=2, random_page_cost=4, JIT on, and synchronous_commit on. The engine's
default configuration was English, but **all measured expressions explicitly
used simple**. The existing local connection was a superuser; the harness uses
CREATE/schema-owner operations and does not require superuser SQL. A separate
limited-role run was not performed.

[scale.json](results/scale.json) records 10K/100K/1M rows, four columns and four
workspaces; [wide.json](results/wide.json) records the independent 16-column run
and 20 MiB probes. The seed is 2022. SELECT/MULTI_SELECT are represented as the
application's text/JSONB storage types. Normal performance rows are small; large
nested documents are an adversarial correctness corpus, not a mixed-size
production workload. Selective cases have 100 measured samples. Broad and
no-trigram cases have **three** samples, so their p95/p99 are the maximum of
three observations, not reliable tail estimates. Every million-row selective
case below returns 50 full rows. Zero-trigram queries return none.

| One-million-row query | Prototype | p50 ms | p95 ms | p99 ms |
| --- | --- | ---: | ---: | ---: |
| Required term | Direct full-text | 6.82 | 7.78 | 7.91 |
| Quoted phrase | Direct full-text | 1,866.93 | 2,349.78 | 2,702.21 |
| Broad term | Direct full-text | 811.94 | 818.55 | 818.55 |
| Selective literal | Direct trigram | 96.70 | 112.08 | 119.06 |
| Zero-trigram literal | Direct trigram | 505.23 | 521.38 | 521.38 |
| Required term | Chunk projection | 0.73 | 0.89 | 1.02 |
| Quoted phrase | Chunk projection | 36.70 | 122.06 | 124.53 |
| Broad term | Chunk projection | 302.85 | 307.92 | 307.92 |
| Selective literal | Chunk projection | 3.96 | 4.84 | 5.17 |
| Zero-trigram literal | Chunk projection | 997.14 | 2,436.13 | 2,436.13 |

Concurrent required-term p95 was 13.55 ms for direct full-text and 3.95 ms for
the projection; direct literal p95 was 105.92 ms. Each has its own sustained
writer samples in the raw evidence. Bulk source generation/import throughput
was approximately 428K, 405K and 381K rows/s at 10K, 100K and 1M respectively.
At one million rows, direct full-text GIN build took 5.97 s, trigram GIN build
8.91 s, projection population 40.54 s and both projection GIN builds 11.40 s.

The one-million-row source heap at the projection stage was 252.51 MiB;
the projection heaps, B-trees and GIN together occupied **2,927.10 MiB**.
This is **11.59x** the source heap, exceeding the proposed 10x storage budget.
The two projection GIN indexes alone occupied 168.87 MiB; projection primary
keys occupied about 1,109.98 MiB. The per-value row/key representation needs
compaction before approval for production. Source and projection relation sizes
were captured after write probes, including dead tuples/pending-list effects;
each stage ran a different number of concurrent probe batches, so these are
observed physical bytes rather than a vacuumed equal-write-count comparison.

Synchronous 100-row insertion batches achieved approximately 31.7K rows/s with
direct full-text indexes, 26.0K with trigram indexes and 598 with projection work
included. Those are 65.4%, 53.7% and 1.2% of the unindexed source throughput.
They demonstrate why the chosen production design needs independent revision
capture and worker publication; they do not establish its write-overhead budget.

At 10K rows with 16 searchable columns, direct phrase p95 was 275.44 ms versus
0.58 ms for the projection; selective literal p95 was 24.45 ms versus 0.45 ms.
The independent large-value run preserved a 20,971,527-byte nested source value
and found its tail phrase across 54,051 prototype chunks. The full-value vector
still missed that phrase. The distinct-word 20 MiB value reproduced SQLSTATE
`54000`; source storage survived and trigram writes/tail matching succeeded.
These probes also reveal the prototype's expensive materialized-array slicing;
the production worker needs the bounded streaming implementation specified above.

These observations support rejecting direct full-text source indexes and
investigating the fenced chunk design. They do not prove the proposed selective
SLO on deployment hardware, arbitrary tokenizer inputs, wide production schemas,
large-document traffic or the integrated implementation. The storage and
zero-trigram broad-query budgets remain unmet by this prototype. Keep the gates
above in the storage PR and rerun the integrated adapter rather than enabling
this experimental layout.
