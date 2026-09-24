# Semantic search acceptance (ENG-1823)

This directory owns cross-service acceptance. Component tests remain in
`tests/integration/`. See [EVIDENCE.md](EVIDENCE.md) for measured results and open
gates. Synthetic vectors prove state/ranking correctness, not semantic quality.

## Deterministic integration

Use an isolated PostgreSQL instance with pgvector and Redis. The repository's
session fixtures create disposable test databases, but also initialize the
instance's default `postgres` database. Do not point these tests at a production
instance or a shared application database. Use a separate application database
for browser QA and migration verification; never stamp a test-created schema as
proof that migrations ran.

```bash
docker compose ls --filter name=tracecat
just cluster up -d --no-seed postgres_db redis
just cluster ports
# Use the ports printed above. These were the ports for the recorded run:
export PG_PORT=6732 REDIS_PORT=7679
uv run pytest tests/acceptance/test_semantic_search.py -q
uv run pytest tests/integration/test_search_indexing.py \
  tests/integration/test_search_retrieval.py \
  tests/integration/test_table_search_lifecycle.py \
  tests/integration/test_embedding_configuration.py -q
uv run pytest tests/integration/test_search_storage.py \
  tests/integration/test_search_indexing_storage.py \
  tests/registry/test_core_table.py tests/registry/test_tables_sdk.py \
  tests/unit/test_search_chunking.py tests/unit/test_search_cursors.py \
  tests/unit/test_search_routes.py tests/unit/test_table_search_router.py -q
uv run pytest tests/migration/test_search_storage_migration.py \
  tests/migration/test_table_search_configuration_migration.py -q
uv run pytest tests/unit/test_tables_service.py \
  tests/unit/api/test_api_internal_tables.py -q
pnpm -C frontend test --runInBand src/components/tables/table-search.test.tsx
```

The new journey uses the real chunker, pgvector, Redis, a real ephemeral Temporal
server/dispatcher, and a controlled HTTP provider. Source rows are inserted through
`TablesService`; vectors are produced by indexing, not seeded directly. Search
uses `core.table.search`, the real SDK, FastAPI route, and retrieval service.
Authentication context and the in-process API transport are injected. The indexing
activity uses an injected HTTP client; the production capacity/activity wrapper
is covered separately by existing indexing tests. This is not a substitute for a
full executor/workflow or browser run.

## Controlled capacity

```bash
SEMANTIC_SEARCH_CAPACITY_ROWS=1000 \
SEMANTIC_SEARCH_REPORT=/tmp/semantic-search-capacity.json \
uv run pytest tests/acceptance/test_semantic_search_capacity.py -q
```

Set the row count between 1,000 and 10,000. The default suite skips this test. It
includes one very long document, variable-length short rows, and read/write
probes. The report identifies its limitations: immediate dispatch rather than the
production schedule cadence, probes between indexing turns rather than concurrent
load, whole-process peak RSS, and vector bytes excluding storage overhead.
Do not interpret this report as a deployment capacity guarantee or billed usage.

## Opt-in real-provider relevance

Use an isolated development workspace with a provider configured through existing
AI settings and allowed by its effective model-access rules. Start its normal DSL
worker, Redis, and Temporal. Configure the local process with that deployment's
DB connection and encryption key, and Redis URL; never paste credentials into a
report or commit. Record the release and infrastructure separately.

```bash
uv run --env-file .env python scripts/benchmark/semantic_search.py \
  --organization-id "$EVAL_ORGANIZATION_ID" \
  --workspace-id "$EVAL_WORKSPACE_ID" \
  --allow-provider-calls --rows 20 --k 5 \
  --output /tmp/semantic-search-relevance.json
```

The explicit flag authorizes provider calls by the script. Select a development
workspace deliberately: the script creates a table there and selected text is
sent to its existing provider. It does not read platform credentials or switch
providers on errors. Repeat against configured OpenAI, Gemini and Bedrock
workspaces when validating all adapters' real-provider behavior.

The runner seeds a fixed synthetic corpus, selects title/description, waits for
actual Ready from the deployed worker, and records hit/recall at k, nonrelevant
results, latency, and short/long relevant ranks. It records model budgets,
dimensions, recipe/configuration version and git revision without credentials or
tenant identifiers. The synthetic table remains for UI/workflow inspection.

Nearest-neighbor search has no relevance threshold: unrelated queries still
return nearest candidates. Interpret false positives against the fixed labels.
Capture actual provider calls/tokens, worker memory, total storage, concurrent
ordinary-operation latency and production-cadence freshness separately. A large
backfill can exceed the default 30-minute wait; timeout is a failed/incomplete
measurement, not an empty successful result.

## Live demonstration

1. Follow `tracecat-qa` to start/reuse the isolated cluster and open the Caddy URL
   from `just cluster ports` in the in-app browser.
2. Configure an eligible provider using existing AI settings; select title and
   description from the TEXT column menus. Keep status unselected.
3. Insert short records and a long row with a relevant passage near the end. Wait
   for Ready. Run `core.table.search` in an actual workflow using a paraphrase;
   inspect distinct IDs, bounded excerpts, offsets and pagination.
4. Edit selected and unselected fields, shrink/delete rows, rename/remove columns,
   and change provider/model. Verify invalidation, rebuild and no stale matches.
5. Repeat with provider/workers unavailable; verify literal search, ordinary
   writes, CSV, schema sync, lookup and unique-index/upsert behavior.
6. Capture screenshots, browser/network errors, typed failures and service logs.
   A missing provider or failed app shell is a blocker, not a passed demo.

The operator procedure is in `docs/self-hosting/semantic-search.mdx`. This ticket
prepares a rollout; it does not authorize production enablement or releases.
