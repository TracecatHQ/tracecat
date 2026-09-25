# ENG-1823 acceptance evidence

Status: **not ready for enablement**. Deterministic acceptance passes; real-provider
quality and the positive live workflow demonstration remain open.

Recorded 2026-09-24 against implementation base `e38eb2bcc` plus this PR, using
Python 3.12.13, local PostgreSQL 16.14/pgvector 0.8.6, Redis 7, and an ephemeral
Temporal server (CLI 1.8.3, server 1.31.2). The application cluster uses Temporal
1.27.1. No production system was changed.

## Results

| Gate | Evidence | Result |
| --- | --- | --- |
| Provider selection, indexing, retrieval, lifecycle | Four existing integration suites; 98 tests | Pass |
| Storage/fencing, SDK, chunking, cursors, routes, table controls | Eight existing suites; 210 tests | Pass |
| Ordinary table service/internal API | Two existing regression suites; 170 tests | Pass |
| Extension migration and providerless selection | Three live migration tests, including guarded downgrade | Pass |
| Cross-service journey | New test: two columns, long tail, action/SDK/route, ranking, continuation, selected/unselected edits, shrink/delete | Pass |
| Pause/outage/rollback recovery | New journey: paused writes, typed query failure, HTTP 429 recovery, SQL write bypassing current tracking followed by explicit reindex | Pass; no old release deployed |
| Temporal payloads | Sampled dispatcher histories bounded to 30 events; no fixture source/query text, embedding fields or credential keys | Pass for this harness |
| Capacity | 1,000 rows, one very long row, 3,683 total chunks | Pass with limitations below |
| UI component behavior | Existing semantic-search suite, 22 tests | Pass |
| Live UI without provider | Created title/description/status table, observed Unavailable and disabled selection, followed AI settings, inserted row successfully | Pass |
| Live positive UI and executor workflow | All provider cards in the new development organization show Not connected | Blocked on eligible provider |
| Real-provider semantic relevance | Opt-in runner provided; no paid provider calls made | Not run |
| Python checks | Ruff lint/format and full basedpyright, zero errors/warnings | Pass |
| Frontend checks | Biome and TypeScript | Pass; host Node 24 warns against requested Node 22 |
| Docs | Regenerated action reference, Mintlify preview, no broken links | Pass; full-page capture is corrupted by the in-app browser, so PR-hosted full-page screenshots remain pending |

The live browser was Codex's in-app browser at the Caddy URL
`http://localhost:1380`. The referenced browser-control skill was absent from the
installed catalog/files; the available CUA in-app browser API was used. No Chrome
fallback was needed. The row dialog emitted React controlled/uncontrolled-input
warnings; the inserted values persisted. These warnings have not been fixed or
classified as semantic-search regressions.

## Integration fix

Fresh deployment initially failed because Alembic had two heads:
`8c0e18190001` and `acbacbf8ef53`. A schema-neutral merge revision
`4489c90f469b` joins the independent search-selection and agent-backend branches.
A fresh application database migrated successfully to this single head. No
existing migration, source data, or database volume was removed.

The repository test fixtures also initialized the cluster's default database
without Alembic history. Browser QA therefore uses a separate application
database. A temporary, uncommitted API port override avoided a port conflict with
another worktree. Neither is a product schema change.

## Controlled capacity measurement

| Measurement | Observed |
| --- | ---: |
| Source rows | 1,000 |
| Embedded chunks | 3,683 |
| Vector-value bytes | 22,643,084 |
| Indexing time with immediate dispatch | 349.63 s |
| Controlled HTTP calls including queries | 1,124 |
| Search p50 / p95, 20 samples | 147.56 / 304.65 ms |
| Baseline row-read p50 / p95, 20 samples | 0.67 / 1.42 ms |
| During-backfill row-read p50 / p95, 111 samples | 1.02 / 2.11 ms |
| Baseline row-write p50 / p95, 20 samples | 4.02 / 6.30 ms |
| During-backfill row-write p50 / p95, 111 samples | 4.69 / 12.30 ms |
| Entire pytest process peak RSS on macOS | 462,831,616 bytes |

This run used controlled 1,536-dimensional vectors, not OpenAI's model. Billed
usage is unknown, not the fake provider's token count. Probes ran between worker
turns, not concurrently; peak RSS includes fixtures and the HTTP server and does
not establish a worker memory bound. Storage excludes indexes/heap overhead and
backups. Immediate dispatch omits the normal ten-second schedule cadence.
Production backfill/freshness and concurrent-load budgets are therefore unproven.
The new runner supports 10,000 rows, but that size has not been measured.

## Deployment evidence and remaining gates

Fargate's checked-in RDS documentation requires administrator extension
provisioning before migrations. Read-only review of `TracecatHQ/k8s` at
`7d47da7d4c04676633397e256b203ad5e5f8623f` confirmed external PostgreSQL and a
migration job running `alembic upgrade head`. No Kubernetes code change was
identified for this database contract; actual extension availability, role
permissions, deployed writer versions and worker readiness still need
per-environment verification. No external deployment PR was created.

Before closing the ticket or enabling the feature:

- Configure an eligible development provider and run the positive UI/full workflow
  journey and real-provider relevance evaluator. Attach model/config/version,
  retrieval success, nonrelevant results and short/long rank evidence.
- Measure real provider usage, normal schedule freshness, concurrent table
  read/write latency and worker memory/history under deployment-like load.
- Expand the acceptance fault matrix to actual process crashes at each publication
  boundary and a live two-workspace/two-model scenario. Existing component suites
  cover many state-level races; that is not full process-failure evidence.
- Verify operator pause/recovery and old-writer rollback/reindex on a deployment-like
  environment. The new integrated test covers pause/recovery and SQL writes bypassing tracking; no old release was deployed.
- Verify each target environment's pgvector, all writers, and worker schedule before
  approving an internal rollout. Preserve source data throughout.

Commands and fixture boundaries are in [README.md](README.md). These open gates
must not be silently counted as passing because the deterministic suites pass.
