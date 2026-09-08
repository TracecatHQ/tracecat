# ENG-1750: case aggregation plan

Branch: `alee/eng-1750`

Researched on 2026-09-08 against GitHub main at `aa71f21d7`. This document records the approved implementation plan. Implementation and
validation followed on the same branch.

## The simple version

Imagine cases are cards in a box. We want to ask, “How many cards have each
status?” or “What is the total cost for each priority?”

The tools for choosing cards, sorting them into piles, and doing the maths
already exist. This ticket connects those tools to cases. The database does
the counting and sends back a small answer.

1. Read the question and check that it makes sense.
2. Pick only this workspace's cases and apply the requested filters.
3. Add custom-field values when the question needs them. Keep cases with
   missing values in an “empty” pile.
4. Ask PostgreSQL to group and calculate in one query.
5. Return the answer, say whether more groups exist, and stop expensive queries.
6. Check the answers with small examples where we already know the right result.

## Where we are

At planning time, all six foundation and sibling implementation PRs below were
merged and ancestors of this branch. ENG-1750 was Todo and its three direct
prerequisites were Done. The service and internal route are now implemented
locally on this branch; the Linear issue has not been updated.

| Ticket | What already exists | Merged PR |
| --- | --- | --- |
| [ENG-1744](https://linear.app/tracecat/issue/ENG-1744) | Shared filters, validation, typed values, and SQL predicates | [#3376](https://github.com/TracecatHQ/tracecat/pull/3376) |
| [ENG-1745](https://linear.app/tracecat/issue/ENG-1745) | Grouping, calculations, time buckets, ordering, and limits | [#3380](https://github.com/TracecatHQ/tracecat/pull/3380) |
| [ENG-1746](https://linear.app/tracecat/issue/ENG-1746) | Config, statement timeout, and structured query errors | [#3381](https://github.com/TracecatHQ/tracecat/pull/3381) |
| [ENG-1747](https://linear.app/tracecat/issue/ENG-1747) | Table field resolver and common identifier helpers | [#3392](https://github.com/TracecatHQ/tracecat/pull/3392) |
| [ENG-1748](https://linear.app/tracecat/issue/ENG-1748) | Working table aggregation service and endpoint to follow | [#3404](https://github.com/TracecatHQ/tracecat/pull/3404) |
| [ENG-1749](https://linear.app/tracecat/issue/ENG-1749) | Case filter and aggregation resolution, enum ranks, custom-field joins | [#3405](https://github.com/TracecatHQ/tracecat/pull/3405) |

Research covered the descriptions and discussions for ENG-1690 through ENG-1693
and ENG-1744 through ENG-1754, the parent design and review history, and the six
PRs' descriptions, review discussions, and relevant merged code. The parent
design's advertised `plans/eng-1692-groupby-agg-cases-tables.md` is absent from
this checkout; its full text was read through the Linear MCP instead.

## Changes since the original ticket

- **Timeouts return 422.** The original ticket and parts of the design still say
  408. The accepted review in [#3381](https://github.com/TracecatHQ/tracecat/pull/3381#discussion_r3926085839)
  changed this to 422 with `query_timeout`; both apps and the table endpoint
  now use that contract. Preserve it here.
- **Numeric results follow the merged compiler.** `sum(BIGINT)` now returns a
  float8 number; narrower integer sums return bigint. NUMERIC sums, means,
  medians, and NUMERIC min/max return float8. Counts remain integers. Preserve
  Decimal group keys as exact strings, following the precision fix in
  [#3404](https://github.com/TracecatHQ/tracecat/pull/3404#discussion_r3936966025).
- **Missing values must survive negation correctly.** The resolver now uses a
  nullable expression around its EXISTS checks. Reuse it so `NOT` does not
  accidentally include missing custom values. The real four-state regression
  was deferred during [#3405](https://github.com/TracecatHQ/tracecat/pull/3405#discussion_r3937181889);
  cover it here.
- **URL custom fields are special.** They are JSONB-backed objects, but the
  resolver extracts their `url` member as text. Ordinary JSONB and MULTI_SELECT
  remain unsupported. Do not replace this with a blanket JSONB rejection.
- **Keep the existing compiler boundary.** Start with a simple scoped select,
  and explicitly pass `base_has_multi_valued_join=False`. The custom-fields
  join is one-to-one. The source-identity limitation discussed on ENG-1745
  concerns repeated relationships and does not block this ticket.
- **Do not copy a retry wrapper.** Review removed it from the table endpoint.
  Unexpected database failures must reach the sanitized central error handler.

## Implementation steps

### 1. Define the question and answer

In `tracecat/cases/schemas.py`, add `CaseAggregateRequest(AggregationSpec)` with
the shared filter tree and a config-backed limit: default 100, maximum 1000,
minimum 1. Reject invalid limits with 422; never silently reduce them.

Add a case response model with the established `{groups, truncated}` shape.
Use a closed scalar union including UUID, Decimal, datetime, date, strings,
booleans, numbers, and null. Dynamic output aliases justify dictionaries for
group rows. Preserve lowercase enum values, UUID strings, exact decimal group
keys, date-only strings, and UTC timestamp strings ending in `Z`. Normalize
aware timestamps to UTC at the response boundary if needed.

Document the existing float8 precision tradeoff and the 256-character text-key
grouping rule in the request/endpoint description. Keep the table response
model unchanged; its value union currently does not include UUID.

### 2. Connect the existing case resolver to the compiler

Add `CasesService.aggregate_cases(request)` in `tracecat/cases/service.py`:

- Load schema metadata with `self.fields.get_field_schema()` and construct one
  `CaseFieldResolver` for the request. This metadata lookup is separate from
  the single SQL query that computes the result.
- Start from a fresh select over `Case` with the explicit
  `Case.workspace_id == self.workspace_id` predicate. Do not reuse the
  paginated search query or hydrate full case objects.
- Apply `compile_filter` when filters are present.
- Resolve every field mentioned in either `group_by` or aggregate targets.
  Collect join specifications by their stable key and add each only once.
  Use the resolver's LEFT JOIN even when custom fields appear only in an
  aggregate. Filter-only references retain their correlated predicates.
- Unknown custom fields fail validation before touching a physical table;
  a workspace with no custom-field definitions remains safe.
- Pass the resolved field mapping, request limit, `entity_id=Case.id`, and
  `base_has_multi_valued_join=False` into `compile_aggregation`.
- Execute inside a transaction/savepoint and `query_execution_context` on
  the same session, following the table service's transaction pattern. Let
  typed timeout/overflow exceptions propagate and roll back failed execution.
- Consume mappings, inspect the extra row, and return at most `limit` groups
  with the correct `truncated` flag. No Python grouping or per-case reads.

As the service starts consuming the resolver's join contracts, move the case
query dataclasses into `tracecat/cases/types.py` and update imports. This is the
small type-layer cleanup flagged by the final review on #3405; preserve their
behavior and frozen/slotted definitions.

### 3. Add the internal door

Add `POST /internal/cases/aggregate` to `tracecat/cases/internal_router.py`,
using `ExecutorWorkspaceRole`, `AsyncDBSession`, and `@require_scope("case:read")`.
Keep it excluded from public OpenAPI, like the existing internal router.

Map semantic validation errors to 400. Preserve the central structured
`query_numeric_overflow` response (400) by letting that subclass escape the
general validation catch. Let the shared timeout handler return 422 with
`query_timeout`. Request-shape failures also return 422. Unexpected database
errors stay sanitized server errors.

### 4. Prove the answers

Add isolated schema/router tests and real PostgreSQL integration tests.
Use existing cases fixtures and the table aggregation tests as references.

| Area | Required examples |
| --- | --- |
| Fixed fields | Counts by status/priority/severity; lowercase JSON; enum declaration ordering; assignee UUID and null |
| Severity filters | `gte high` includes high, critical, fatal; excludes unknown/other; status ranges and invalid enum values return 400 |
| Custom fields | Grouping with stored null and absent backing rows; `is_null` exactly agrees with the null group; multiple fields use one LEFT JOIN; aggregate-only custom target works |
| Negation | Matching, different, stored-null, and absent-row states under NOT, AND, and OR; missing values retain SQL null semantics |
| Supported values | All seven calculations on numeric custom fields; URL extraction; SELECT/BOOLEAN/text grouping; 256-character prefix collapse; exact decimal group keys |
| Time | Hour/day/week/month; non-UTC zone and DST; week/month boundaries; UTC `Z` output; custom DATE buckets stay date-only |
| Limits and ordering | Defaults, aliases, ties, nulls last, grand total, empty input, empty `in`/`not_in`, `min_count`, exact limit and limit+1, 1001 rejected |
| Errors and access | Invalid field/type/bucket; no custom schema; real 1 ms timeout gives structured 422; overflow gives structured 400; unauthorized callers cannot read |
| Isolation | Two workspaces with overlapping values; foreign cases excluded even with RLS off; custom schema selected from the authenticated workspace |

Assert serialized HTTP bodies for UUID, enum, Decimal, numeric, and timestamp
results. Exercise a real timeout deterministically with a test-local slow SQL
expression; do not rely on a small aggregation randomly taking over 1 ms.

Before database testing, check `docker compose ls --filter name=tracecat` and
choose the existing stack or the worktree cluster. Use `just cluster up -d`
and `just cluster ports` as appropriate; keep existing volumes.

Run the new integration and internal-route tests, adjacent case/query/table
regressions, then the required Ruff autofix/check/format and BasedPyright
checks. Verify the internal route does not change the public generated client.

## Follow-up work stays separate

- [ENG-1751](https://linear.app/tracecat/issue/ENG-1751): dropdowns and tags.
- [ENG-1753](https://linear.app/tracecat/issue/ENG-1753): cases workflow action
  and SDK method; now can build on this endpoint once it lands.
- [ENG-1752](https://linear.app/tracecat/issue/ENG-1752): matching table action.
- [ENG-1754](https://linear.app/tracecat/issue/ENG-1754): action docs, tool lists,
  and the PostgreSQL minimum-version documentation.
- [ENG-1691](https://linear.app/tracecat/issue/ENG-1691) and
  [ENG-1693](https://linear.app/tracecat/issue/ENG-1693): search improvements.

No migration, infrastructure change, new UI, or rewrite of the existing
`GET /cases/search/aggregate` is needed. Done means the authenticated internal
cases endpoint returns correct, group-count-limited, workspace-isolated results and the
database/HTTP regression coverage above passes.

## Adversarial review follow-through

- `min_count` accepts positive values through PostgreSQL's signed BIGINT maximum
  (`2**63 - 1`). The shared compiler types row counts as BIGINT so both ordinary
  and distinct-case HAVING thresholds bind correctly. Larger inputs return 422
  before execution for cases and tables.
- Successful aggregation restores the transaction's previous statement timeout.
  Failed execution relies on the caller's transaction/savepoint rollback, which
  preserves the original database error and restores the previous setting.
- The group limit is not a response-byte limit. The 256-character prefix rule
  applies only to text grouping keys; text min/max returns the full value. A
  byte budget needs a separate API decision about the limit and rejection error;
  silently truncating aggregate values would change the answer.
- ENG-1751 must retain workspace predicates throughout dropdown/tag joins and
  mark tag-backed fields as multi-valued. The compiler then counts distinct case
  IDs and rejects calculations that would be inflated by duplicate join rows.
  The current custom-fields join remains one-to-one.
