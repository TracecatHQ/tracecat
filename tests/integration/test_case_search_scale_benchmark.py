"""Opt-in measurement: case search latency and vector migration cost at scale.

Seeds two workspaces in a disposable database, applies the stored-vector
migration (backfill plus concurrent index builds) and times it, then runs the
production search expressions for broad, typo-matched, and rare terms over all
time and a 30-day window. Each search times the relevance-ordered first page
and the exact count the UI's status totals use. Run against an isolated test
PostgreSQL service:

    PG_PORT=5432 TRACECAT_RUN_CASE_SEARCH_SCALE_BENCH=1 uv run pytest \
        tests/integration/test_case_search_scale_benchmark.py -s

TRACECAT_CASE_SEARCH_SCALE_CASES sets cases per workspace (default 100000; a
second workspace gets the same number). TRACECAT_CASE_SEARCH_SCALE_TIMEOUT_MS
sets the per-statement budget (default 30000). Results and EXPLAIN plans go to
the ignored .worktrees/measurements/case-search-scale/ directory. These are
local warm-cache numbers, not production RDS latency.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import orjson
import pytest
import sqlalchemy as sa
from psycopg.errors import QueryCanceled
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from tests.integration.test_case_text_search_postgres import (
    INDEX_NAMES,
    OTHER_WORKSPACE,
    WORKSPACE,
    apply_search_migration,
    apply_vector_migration,
)
from tracecat.cases.text_search import (
    build_case_text_expressions,
    parse_case_text_query,
)
from tracecat.db.models import Case

pytest_plugins = ["tests.integration.test_case_text_search_postgres"]

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.skipif(
        os.environ.get("TRACECAT_RUN_CASE_SEARCH_SCALE_BENCH") != "1",
        reason="Set TRACECAT_RUN_CASE_SEARCH_SCALE_BENCH=1 for scale measurements",
    ),
]

PAGE_SIZE = 21  # A 20-row page plus the paginator's lookahead row.
EXPLAIN_ANALYZE_MAX_MS = 10_000
OUTPUT_DIR = (
    Path(__file__).resolve().parents[2] / ".worktrees/measurements/case-search-scale"
)
# Word lengths decide typo expansion: 5-32 ASCII letters are fuzzy, shorter are not.
TERMS = (
    ("broad_exact", "dns"),
    ("broad_fuzzy", "network"),
    ("two_fuzzy_words", "phishing invoice"),
    ("selective_fuzzy", "lsass"),
)
WINDOWS: tuple[tuple[str, timedelta | None], ...] = (
    ("all_time", None),
    ("30_days", timedelta(days=30)),
)
SEED_SQL = """
INSERT INTO "case" (id, workspace_id, case_number, summary, description, created_at)
SELECT md5('scale-case-' || g::text)::uuid,
    CASE WHEN g <= :count THEN CAST(:workspace AS uuid) ELSE CAST(:other AS uuid) END,
    g,
    CASE WHEN g % 997 = 0 THEN 'Credential dumping via lsass on host-' || g % 500
        ELSE (ARRAY['Suspicious network connection', 'Phishing email reported',
            'Malware detected on host', 'Failed login burst',
            'PowerShell encoded command', 'Unusual DNS query',
            'Privilege escalation attempt', 'Data exfiltration over network'
        ])[1 + g % 8] || ' on host-' || g % 500
    END,
    repeat(
        (ARRAY[
            'Sensor alert for host-' || g % 500 || ' on network segment ' || g % 20 || '. ',
            'User reported a phishing message with attachment invoice-' || g || '.pdf. ',
            'Endpoint telemetry shows an unusual process tree. ',
            'Firewall blocked outbound DNS traffic to 10.' || g % 255 || '.' || g % 7 || '.1. '
        ])[1 + (g / 8) % 4],
        -- 10% of descriptions exceed 2 KB, which takes the slow tokenizer path.
        CASE WHEN g % 10 = 0 THEN 60 WHEN g % 10 < 4 THEN 20 ELSE 3 END
    ) || CASE WHEN g % 20 = 0 THEN ' Note: résumé attached ✓' ELSE '' END,
    now() - ((g * 7919) % 525600) * interval '1 minute'
FROM generate_series(1, 2 * :count) g
"""


type QueryKind = Literal["page", "count"]


@dataclass(frozen=True, slots=True)
class Measurement:
    term: str
    window: str
    query: QueryKind
    elapsed_ms: float | None
    timed_out: bool
    rows: int | None


@dataclass(frozen=True, slots=True)
class ScaleReport:
    postgres_version: str
    cases_per_workspace: int
    workspaces: int
    vector_migration_seconds: float
    timeout_ms: int
    measurements: list[Measurement]


def _statements(
    term: str, cutoff: datetime | None
) -> tuple[tuple[QueryKind, sa.Select[tuple]], ...]:
    """Build the page and count queries from the production expression builder."""
    expressions = build_case_text_expressions(parse_case_text_query(term))
    scope: list[sa.ColumnElement[bool]] = [
        Case.workspace_id == WORKSPACE,
        expressions.predicate,
    ]
    if cutoff is not None:
        scope.append(Case.created_at >= cutoff)
    page = (
        sa.select(Case.id, expressions.score)
        .where(*scope)
        .order_by(expressions.score.desc(), Case.id.desc())
        .limit(PAGE_SIZE)
    )
    count = sa.select(sa.func.count()).select_from(Case).where(*scope)
    return (("page", page), ("count", count))


def _session_settings(conn: sa.Connection, timeout_ms: int) -> None:
    conn.execute(sa.text(f"SET LOCAL statement_timeout = {timeout_ms}"))
    conn.execute(sa.text("SET LOCAL pg_trgm.strict_word_similarity_threshold = 0.65"))


def _explain(
    engine: Engine, statement: sa.Select[tuple], *, analyze: bool, timeout_ms: int
) -> str:
    options = "ANALYZE, BUFFERS" if analyze else "COSTS"
    with engine.connect() as conn, conn.begin():
        _session_settings(conn, timeout_ms)
        compiled = statement.compile(dialect=conn.dialect)
        rows = conn.exec_driver_sql(f"EXPLAIN ({options}) {compiled}", compiled.params)
        return "\n".join(row[0] for row in rows)


def _measure(
    engine: Engine, statement: sa.Select[tuple], timeout_ms: int
) -> tuple[float | None, list[tuple], str]:
    """Time one real execution, then capture the plan that produced it."""
    with engine.connect() as conn, conn.begin():
        _session_settings(conn, timeout_ms)
        started = time.perf_counter()
        try:
            rows = [tuple(row) for row in conn.execute(statement)]
        except DBAPIError as exc:
            if not isinstance(exc.orig, QueryCanceled):
                raise
            plan = _explain(engine, statement, analyze=False, timeout_ms=timeout_ms)
            return None, [], plan
        elapsed_ms = (time.perf_counter() - started) * 1000
    analyze = elapsed_ms <= EXPLAIN_ANALYZE_MAX_MS
    plan = _explain(engine, statement, analyze=analyze, timeout_ms=timeout_ms)
    return round(elapsed_ms, 1), rows, plan


def _seed(engine: Engine, count: int) -> float:
    """Load cases as old-version writes, then time the stored-vector migration."""
    apply_vector_migration(engine, downgrade=True)
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for name in sorted(INDEX_NAMES):
            conn.execute(sa.text(f'DROP INDEX public."{name}"'))
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                'ALTER TABLE "case" ADD COLUMN created_at timestamptz NOT NULL DEFAULT now()'
            )
        )
        conn.execute(
            sa.text(
                'CREATE INDEX ix_case_cursor_pagination ON "case" (workspace_id, created_at, id)'
            )
        )
        conn.execute(
            sa.text(SEED_SQL),
            {"count": count, "workspace": WORKSPACE, "other": OTHER_WORKSPACE},
        )
    apply_search_migration(engine)
    started = time.perf_counter()
    apply_vector_migration(engine)
    return round(time.perf_counter() - started, 1)


def test_case_search_at_scale(search_database: Engine) -> None:
    count = int(os.environ.get("TRACECAT_CASE_SEARCH_SCALE_CASES") or 100_000)
    timeout_ms = int(os.environ.get("TRACECAT_CASE_SEARCH_SCALE_TIMEOUT_MS") or 30_000)
    migration_s = _seed(search_database, count)
    cutoff_now = datetime.now(UTC)
    with search_database.begin() as conn:
        postgres_version = str(conn.scalar(sa.text("SELECT version()")))

    measurements: list[Measurement] = []
    plans: list[str] = []
    for term_name, term in TERMS:
        for window_name, window in WINDOWS:
            cutoff = cutoff_now - window if window is not None else None
            for query, statement in _statements(term, cutoff):
                elapsed_ms, rows, plan = _measure(
                    search_database, statement, timeout_ms
                )
                # The page returns rows; the count returns one row holding the total.
                returned = (
                    len(rows) if query == "page" else (rows[0][0] if rows else None)
                )
                measurement = Measurement(
                    term=term_name,
                    window=window_name,
                    query=query,
                    elapsed_ms=elapsed_ms,
                    timed_out=elapsed_ms is None,
                    rows=returned,
                )
                measurements.append(measurement)
                shown = "TIMEOUT" if elapsed_ms is None else f"{elapsed_ms:>9.1f} ms"
                plans.append(
                    f"=== {term_name} / {window_name} / {query} "
                    f"({'timeout' if elapsed_ms is None else f'{elapsed_ms} ms'}) ===\n{plan}"
                )
                print(
                    f"{term_name:<18} {window_name:<9} {query:<6} {shown:>12}"
                    f"  rows={measurement.rows}"
                )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    report = ScaleReport(
        postgres_version=postgres_version,
        cases_per_workspace=count,
        workspaces=2,
        vector_migration_seconds=migration_s,
        timeout_ms=timeout_ms,
        measurements=measurements,
    )
    (OUTPUT_DIR / "results.json").write_bytes(
        orjson.dumps(report, option=orjson.OPT_INDENT_2)
    )
    (OUTPUT_DIR / "plans.txt").write_text("\n\n".join(plans))
    print(f"\nvector migration on {2 * count} cases: {migration_s}s")
    print(f"wrote {OUTPUT_DIR}/results.json and plans.txt")
