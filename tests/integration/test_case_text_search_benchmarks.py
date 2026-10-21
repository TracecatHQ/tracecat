"""Opt-in, reproducible synthetic latency/index/write-cost measurements.

Run against an isolated test PostgreSQL service:

    PG_PORT=6932 TRACECAT_RUN_CASE_TEXT_BENCHMARKS=1 uv run pytest \
        tests/integration/test_case_text_search_benchmarks.py -s

TRACECAT_CASE_TEXT_BENCHMARK_CASES defaults to 20000, and
TRACECAT_CASE_TEXT_BENCHMARK_OUTPUT defaults to the ignored
.worktrees/measurements/case-text-search.json. Timings are measured in one
disposable database, first without the four search indexes, then with them.
Writes roll back identical 500-row transactions; query results are never capped
before workspace/filter predicates. These local warm-cache numbers do not
represent production RDS performance or concurrent workloads.
"""

from __future__ import annotations

import math
import os
import statistics
import time
from dataclasses import dataclass
from pathlib import Path

import orjson
import pytest
import sqlalchemy as sa
from psycopg.errors import QueryCanceled
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from tests.integration.test_case_text_search_postgres import (
    INDEX_NAMES,
    WORKSPACE,
    apply_search_migration,
    parse_fixture_query,
    search_statement,
    seed_case,
)
from tracecat.cases.text_search import get_case_search_excerpts
from tracecat.db.models import Case

pytest_plugins = ["tests.integration.test_case_text_search_postgres"]

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.skipif(
        os.environ.get("TRACECAT_RUN_CASE_TEXT_BENCHMARKS") != "1",
        reason="Set TRACECAT_RUN_CASE_TEXT_BENCHMARKS=1 for synthetic measurements",
    ),
]


@dataclass(frozen=True, slots=True)
class LatencyMeasurement:
    name: str
    indexed: bool
    p50_ms: float
    p95_ms: float
    samples: int
    timeouts: int = 0
    result_count: int | None = None
    plan: str | None = None


@dataclass(frozen=True, slots=True)
class SearchBenchmarkReport:
    postgres_version: str
    case_count: int
    workspace_count: int
    index_bytes: dict[str, int]
    index_build_ms: float
    queries: list[LatencyMeasurement]
    writes: list[LatencyMeasurement]
    excerpts: list[LatencyMeasurement]
    relevance_checks: dict[str, bool]
    assessment: str


def _measurement(
    name: str,
    indexed: bool,
    latencies: list[float],
    *,
    timeouts: int = 0,
    result_count: int | None = None,
    plan: str | None = None,
) -> LatencyMeasurement:
    ordered = sorted(latencies)
    p95 = ordered[math.ceil(len(ordered) * 0.95) - 1]
    return LatencyMeasurement(
        name,
        indexed,
        round(statistics.median(ordered), 3),
        round(p95, 3),
        len(ordered),
        timeouts,
        result_count,
        plan,
    )


def _measure_queries(engine: Engine, *, indexed: bool) -> list[LatencyMeasurement]:
    results: list[LatencyMeasurement] = []
    queries = [
        ("selective_multiword", "powershell encoded", ()),
        ("selective_phrase", '"encoded command"', ()),
        ("typo", "powershel", ()),
        ("literal", "literal:encoded command", ()),
        ("broad", "network", ()),
        ("broad_structured_filter", "network", (Case.case_number <= 100,)),
    ]
    for name, query, filters in queries:
        statement = search_statement(query, filters=filters, limit=50)
        latencies: list[float] = []
        timeouts = 0
        result_count: int | None = None
        plan: str | None = None
        for sample in range(8):
            with engine.connect() as conn:
                transaction = conn.begin()
                conn.execute(sa.text("SET LOCAL statement_timeout = 1500"))
                conn.execute(
                    sa.text("SET LOCAL pg_trgm.strict_word_similarity_threshold = 0.65")
                )
                started = time.perf_counter()
                try:
                    rows = conn.execute(statement).tuples().all()
                    elapsed = (time.perf_counter() - started) * 1000
                    result_count = len(rows)
                    if sample == 7:
                        compiled = statement.compile(
                            dialect=engine.dialect,
                            compile_kwargs={"literal_binds": True},
                        )
                        sql = str(compiled).replace("%%", "%")
                        plan = "\n".join(
                            conn.execute(sa.text(f"EXPLAIN {sql}")).scalars()
                        )
                except DBAPIError as exc:
                    if not isinstance(exc.orig, QueryCanceled):
                        raise
                    elapsed = (time.perf_counter() - started) * 1000
                    timeouts += int(sample > 0)
                finally:
                    transaction.rollback()
                # Sample zero warms the same workload and is excluded.
                if sample:
                    latencies.append(elapsed)
        results.append(
            _measurement(
                name,
                indexed,
                latencies,
                timeouts=timeouts,
                result_count=result_count,
                plan=plan,
            )
        )
    return results


def _measure_writes(
    engine: Engine, *, indexed: bool, count: int
) -> list[LatencyMeasurement]:
    statements = [
        (
            "insert_500",
            sa.text("""INSERT INTO "case" (id, workspace_id, case_number, summary, description)
            SELECT md5('synthetic-write-' || g::text)::uuid, :workspace, :count + g,
                'synthetic network alert', repeat('routine synthetic activity ', 20)
            FROM generate_series(1, 500) g"""),
        ),
        (
            "update_500",
            sa.text("""UPDATE "case" SET summary = summary || ' measured',
            description = description || ' measured' WHERE case_number BETWEEN 1 AND 500"""),
        ),
    ]
    measurements: list[LatencyMeasurement] = []
    for name, statement in statements:
        latencies: list[float] = []
        for sample in range(8):
            with engine.connect() as conn:
                transaction = conn.begin()
                started = time.perf_counter()
                conn.execute(statement, {"workspace": WORKSPACE, "count": count})
                elapsed = (time.perf_counter() - started) * 1000
                transaction.rollback()
                if sample:
                    latencies.append(elapsed)
        measurements.append(_measurement(name, indexed, latencies))
    return measurements


async def _measure_excerpt_batches(engine: Engine) -> list[LatencyMeasurement]:
    measurements: list[LatencyMeasurement] = []
    async_engine = create_async_engine(
        engine.url.set(drivername="postgresql+asyncpg"), poolclass=NullPool
    )
    try:
        async with AsyncSession(async_engine) as session:
            for name, value in (
                ("exact_excerpt_batch", "powershell encoded"),
                ("fuzzy_excerpt_batch", "powershellx"),
            ):
                with engine.begin() as conn:
                    conn.execute(
                        sa.text(
                            "SET LOCAL pg_trgm.strict_word_similarity_threshold = 0.65"
                        )
                    )
                    case_ids = list(
                        conn.execute(search_statement(value, limit=50)).scalars()
                    )
                latencies: list[float] = []
                for sample in range(8):
                    started = time.perf_counter()
                    excerpts = await get_case_search_excerpts(
                        session,
                        workspace_id=WORKSPACE,
                        case_ids=case_ids,
                        query=parse_fixture_query(value),
                    )
                    elapsed = (time.perf_counter() - started) * 1000
                    assert set(excerpts) == set(case_ids)
                    assert all(
                        len(excerpt.text) <= 240
                        for values in excerpts.values()
                        for excerpt in values
                    )
                    if sample:
                        latencies.append(elapsed)
                measurements.append(
                    _measurement(name, True, latencies, result_count=len(case_ids))
                )
    finally:
        await async_engine.dispose()
    return measurements


@pytest.mark.anyio
async def test_synthetic_search_measurements(search_database: Engine) -> None:
    count = int(os.environ.get("TRACECAT_CASE_TEXT_BENCHMARK_CASES") or 20000)
    assert 1000 <= count <= 200000
    # Compare identical final predicates with/without indexes; retain their
    # localized functions when measuring the unindexed baseline.
    with search_database.connect().execution_options(
        isolation_level="AUTOCOMMIT"
    ) as conn:
        for name in sorted(INDEX_NAMES):
            conn.execute(sa.text(f'DROP INDEX public."{name}"'))
    with search_database.begin() as conn:
        conn.execute(
            sa.text("""INSERT INTO "case" (id, workspace_id, case_number, summary, description)
            SELECT md5('synthetic-case-' || g::text)::uuid,
                ('00000000-0000-4000-8000-' || lpad((1 + g % 4)::text, 12, '0'))::uuid,
                g, CASE WHEN g % 137 = 0 THEN 'PowerShell'
                        WHEN g % 313 = 0 THEN 'Credential access'
                        ELSE 'Synthetic network alert' END,
                CASE WHEN g % 137 = 0 THEN 'encoded command detected '
                     ELSE repeat('routine synthetic activity ', 1 + g % 40) END
            FROM generate_series(1, :count) g"""),
            {"count": count},
        )
        conn.execute(sa.text('ANALYZE "case"'))
    queries = _measure_queries(search_database, indexed=False)
    writes = _measure_writes(search_database, indexed=False, count=count)
    started = time.perf_counter()
    apply_search_migration(search_database)
    index_build_ms = (time.perf_counter() - started) * 1000
    with search_database.begin() as conn:
        conn.execute(sa.text('ANALYZE "case"'))
        index_bytes = dict(
            conn.execute(
                sa.text("""SELECT c.relname, pg_relation_size(c.oid)
            FROM pg_class c WHERE c.relname = ANY(:names)"""),
                {"names": sorted(INDEX_NAMES)},
            )
            .tuples()
            .all()
        )
        postgres_version = str(conn.scalar(sa.text("SELECT version()")))
    queries.extend(_measure_queries(search_database, indexed=True))
    writes.extend(_measure_writes(search_database, indexed=True, count=count))
    with search_database.begin() as conn:
        conn.execute(
            sa.text("SET LOCAL pg_trgm.strict_word_similarity_threshold = 0.65")
        )
        exact = seed_case(conn, count + 1, "PowerShell encoded")
        summary = seed_case(conn, count + 2, "PowerShell", "encoded")
        description = seed_case(
            conn, count + 3, "process detected", "PowerShell encoded"
        )
        fuzzy = seed_case(conn, count + 4, "Powershel", "encoded")
        gold = {exact, summary, description, fuzzy}
        rows = [
            row[0]
            for row in conn.execute(search_statement("powershell encoded", limit=500))
            .tuples()
            .all()
            if row[0] in gold
        ]
        phrase_ids = set(
            conn.execute(search_statement('"powershell encoded"', limit=500)).scalars()
        )
        checks = {
            "exact_phrase_ahead_of_split_and_fuzzy": rows[0] == exact,
            "exact_split_ahead_of_fuzzy": rows.index(summary) < rows.index(fuzzy),
            "phrase_does_not_cross_fields": exact in phrase_ids
            and description in phrase_ids
            and summary not in phrase_ids,
            "multiword_recall": set(rows) == gold,
            "typo_recall": exact
            in conn.execute(search_statement("powershel encoded", limit=500)).scalars(),
        }
    assert all(checks.values()), checks
    excerpts = await _measure_excerpt_batches(search_database)
    report = SearchBenchmarkReport(
        postgres_version,
        count + 4,
        4,
        index_bytes,
        round(index_build_ms, 3),
        queries,
        writes,
        excerpts,
        checks,
        "Native rank meets the synthetic exact/phrase/source/fuzzy requirements. It has no corpus-wide inverse document frequency. BM25 is a separate relevance prototype only if representative relevance judgments show a gap; broad-query latency must still be bounded and measured independently. These warm local measurements do not establish an RDS production latency budget.",
    )
    output = Path(
        os.environ.get("TRACECAT_CASE_TEXT_BENCHMARK_OUTPUT")
        or ".worktrees/measurements/case-text-search.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(
        orjson.dumps(
            report, option=orjson.OPT_INDENT_2 | orjson.OPT_SERIALIZE_DATACLASS
        )
    )
    print(f"Synthetic search measurements: {output.resolve()}")


@dataclass(frozen=True, slots=True)
class LargeDocumentBenchmarkReport:
    """Final-design measurements for the supported unbounded VARCHAR contract."""

    source_characters: int
    queries: list[LatencyMeasurement]
    excerpts: list[LatencyMeasurement]
    writes: list[LatencyMeasurement]


@pytest.mark.anyio
async def test_large_document_measurements(search_database: Engine) -> None:
    source = (
        " ".join(f"synthetic{number:06d}" for number in range(90000)) + " alpha beta"
    )
    repeated = "foo " * 17000 + "foo bar"
    expanded = "foo " + "Ⱥ" * 683 + " bar"
    with search_database.begin() as conn:
        large_id = seed_case(conn, 1, "synthetic large", source)
        repeated_id = seed_case(conn, 2, "synthetic repeated", repeated)
        expanded_id = seed_case(conn, 3, "synthetic expansion", expanded)
        conn.execute(sa.text('ANALYZE "case"'))
    measurements: list[LatencyMeasurement] = []
    for label, query, case_id in (
        ("large_phrase", '"alpha beta"', large_id),
        ("large_multiword", "synthetic000001 beta", large_id),
        ("repeated_late_phrase", '"foo bar"', repeated_id),
        ("normalized_overflow_other_word", "foo", expanded_id),
    ):
        latencies: list[float] = []
        statement = search_statement(query, filters=(Case.id == case_id,), limit=1)
        plan: str | None = None
        for sample in range(8):
            with search_database.begin() as conn:
                conn.execute(sa.text("SET LOCAL statement_timeout=1500"))
                conn.execute(
                    sa.text("SET LOCAL pg_trgm.strict_word_similarity_threshold=0.65")
                )
                started = time.perf_counter()
                rows = conn.execute(statement).tuples().all()
                elapsed = (time.perf_counter() - started) * 1000
                assert rows[0][0] == case_id
                if sample:
                    latencies.append(elapsed)
                if sample == 7:
                    compiled = str(
                        statement.compile(
                            dialect=search_database.dialect,
                            compile_kwargs={"literal_binds": True},
                        )
                    ).replace("%%", "%")
                    plan = "\n".join(
                        conn.execute(sa.text("EXPLAIN " + compiled)).scalars()
                    )
        measurements.append(
            _measurement(label, True, latencies, result_count=1, plan=plan)
        )
    writes: list[LatencyMeasurement] = []
    for operation in ("insert", "update"):
        latencies = []
        for sample in range(8):
            with search_database.connect() as conn:
                transaction = conn.begin()
                conn.execute(sa.text("SET LOCAL statement_timeout=1500"))
                started = time.perf_counter()
                if operation == "insert":
                    seed_case(conn, 4, "synthetic inserted", source)
                else:
                    conn.execute(
                        sa.text('UPDATE "case" SET description=:source WHERE id=:id'),
                        {"source": source + " changed", "id": large_id},
                    )
                elapsed = (time.perf_counter() - started) * 1000
                transaction.rollback()
                if sample:
                    latencies.append(elapsed)
        writes.append(_measurement(f"large_{operation}", True, latencies))
    excerpts: list[LatencyMeasurement] = []
    async_engine = create_async_engine(
        search_database.url.set(drivername="postgresql+asyncpg"), poolclass=NullPool
    )
    try:
        for label, query, case_id in (
            ("large_phrase_excerpt", '"alpha beta"', large_id),
            ("repeated_phrase_excerpt", '"foo bar"', repeated_id),
        ):
            latencies = []
            async with (
                async_engine.connect() as connection,
                AsyncSession(connection) as session,
            ):
                for sample in range(8):
                    started = time.perf_counter()
                    results = await get_case_search_excerpts(
                        session,
                        workspace_id=WORKSPACE,
                        case_ids=[case_id],
                        query=parse_fixture_query(query),
                    )
                    elapsed = (time.perf_counter() - started) * 1000
                    assert results[case_id]
                    if sample:
                        latencies.append(elapsed)
            excerpts.append(_measurement(label, True, latencies, result_count=1))
    finally:
        await async_engine.dispose()
    report = LargeDocumentBenchmarkReport(len(source), measurements, excerpts, writes)
    output = Path(".worktrees/measurements/case-text-search-large-final.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(
        orjson.dumps(
            report, option=orjson.OPT_INDENT_2 | orjson.OPT_SERIALIZE_DATACLASS
        )
    )
