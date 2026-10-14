# Table lexical-search storage experiment

ENG-2022 owns this isolated design experiment. The application does not import
these modules. There are no migrations, production indexes, or runtime changes.
Read [DECISION.md](DECISION.md) before using the prototype for another experiment.

## Reproduce

Use PostgreSQL 16 with `pg_trgm` available and synced repository dependencies.
Create a **new empty database** whose name begins `tracecat_search_bench_`. The
benchmark role needs CONNECT, CREATE on that database, and permission to install
the trusted `pg_trgm` extension, or an administrator can install it beforehand.
The runner never requires superuser. Do not point it at the application database.

```sh
export TABLE_SEARCH_BENCH_DSN='postgresql://benchmark_user:synthetic_password@127.0.0.1:5432/tracecat_search_bench_synthetic'
uv run python -m scripts.benchmark.table_search.run \
  --sizes 10000 100000 1000000 --width 4 --workspaces 4 --seed 2022 \
  --samples 100 --broad-samples 10 --write-batches 10 --write-batch-size 100 \
  --environment 'Describe server CPU, RAM, storage, limits and competing workloads' \
  --output /tmp/table-search-scale.json

# Independently exercise a wide schema, using the same empty database after cleanup.
uv run python -m scripts.benchmark.table_search.run \
  --sizes 10000 --width 16 --samples 100 --broad-samples 10 \
  --environment 'Describe the same server and any changed settings' \
  --output /tmp/table-search-wide.json
```

For a quick live correctness run, use `--sizes 100 --samples 2 --broad-samples 1
--write-batches 2 --write-batch-size 10`. Safety probes still run. To exercise
larger source values, set `--max-value-bytes 20971520` (20 MiB). Default safety
values reach roughly 5 MiB, independently of the normal-row performance corpus.

Setup refuses both an incorrectly named database and any existing user relations.
A database advisory lock excludes concurrent runners. Each invocation creates an
unpredictable `tsbench_*` schema and removes **only that schema** in `finally`,
including after SQL failures. It retains the database and extension and never
deletes containers, roles or volumes. DSNs and credentials are not written into
results. A forcibly killed process can leave its synthetic schema behind: inspect
the explicitly selected benchmark database and remove that exact `tsbench_*`
schema manually before rerunning. The guard intentionally refuses to erase it.

## What is measured

All identifiers and values are synthetic. The seeded SQL generator supplies TEXT,
JSONB nested keys/scalars/arrays, SELECT text, and MULTI_SELECT JSONB elements.
`--width` adds TEXT columns; `--workspaces` distributes rows deterministically.
Every source row is searchable independently of semantic-search configuration.

The stages independently measure per-column expression GIN full-text indexes,
per-column expression GIN trigram indexes, and both indexes on a chunk projection.
The source primary key and workspace/ID B-tree exist in all stages. There is no
forced index selection. Full-text indexing and queries explicitly use
`pg_catalog.simple`. JSON full-text candidates use `jsonb_to_tsvector(...,
'["all"]')`; JSON phrase rechecks enumerate individual keys/scalars. Literal
search uses the unchanged PostgreSQL `jsonb::text` serialization, including
punctuation, and the shared explicit-literal escaping policy.

Each whitespace unit uses the shared parser. AND connects units; OR connects
selected columns. Phrase matching never joins separate source values. The
chunk prototype uses maximum chunk score per unit, then adds unit scores, so
overlap cannot multiply a row's score. Scores are experimental and are not a
proposal for identical ranking across physical designs.

The runner records:

- Server version, extension version, relevant settings, declared hardware, seed,
  schema width, row/workspace counts, and timestamps.
- Selective/broad searches, JSON/SELECT/MULTI_SELECT scope, phrases, literal
  fragments, serialized JSON, and a no-trigram literal. Searches return up to
  50 **full source rows**, with deterministic score/ID keyset ordering.
- One warmup, raw client wall-time samples, nearest-rank p50/p95/p99, and
  `EXPLAIN (ANALYZE, BUFFERS, WAL, SETTINGS, FORMAT JSON)` for each query.
  Returned rows, second-page time and page-overlap checks are recorded too.
- Every heap/index's bytes, bulk synthetic import seconds/rows per second,
  projection population time, GIN build time, indexed insert/update batches,
  and insert throughput relative to the unindexed source.
- A second connection inserting/updating/deleting probe rows continuously while
  the selective search samples run. Its row count and latency samples are
  recorded. This models one competing writer, not a production traffic mix.
- Live correctness checks and safety counterexamples for positions, repeated
  words, lexeme length, oversized vectors, indexed-write failure, and source
  preservation after independent projection failure.

The query timeout defaults to 5 seconds; imports and index builds are unbounded.
Timed-out queries are reported separately and excluded from latency quantiles.
If warmup or EXPLAIN times out, no quantile is invented; the configured subsequent
sample count is skipped and reported as `skipped_samples`. Small broad-query
sample counts produce coarse tail quantiles. This is warm-cache evidence with
other local workloads running, not a dedicated capacity benchmark. Bulk import
means server-side synthetic INSERT throughput; it does not measure CSV/API import.
Relation `bytes` reports ordinary heaps/indexes separately; a table's
`total_bytes` includes its indexes, TOAST and auxiliary forks. Do not add a
table's total to its indexes again. Large-document probes establish correctness
rather than capacity.

## Evidence and tests

Committed `results/*.json` preserve raw samples, query SQL/parameters, plans and
settings. [DECISION.md](DECISION.md) gives the storage decision and the storage
PR's correctness/performance gates. Do not interpret a successful timing run as
proof that the prototype supports arbitrary source values.

```sh
uv run pytest --noconftest tests/unit/test_table_search_benchmark.py \
  tests/unit/query/test_lexical.py
uv run ruff check scripts/benchmark/table_search tests/unit/test_table_search_benchmark.py
uv run ruff format --check scripts/benchmark/table_search tests/unit/test_table_search_benchmark.py
```

The exact live contract fixtures run before timing every size. The unit tests
cover database guards, escaping/binding, shared input limits/scope, expression
selection, and stable cursor generation. No application API or UI is enabled.
