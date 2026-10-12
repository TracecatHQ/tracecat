"""Opt-in synthetic large-payload measurements for case-page read options.

    TRACECAT_RUN_CASE_READ_BENCHMARKS=1 uv run pytest \
      tests/integration/test_case_search_read_benchmarks.py -s

Uses the isolated pytest database, 1000 synthetic cases by default, and a
deterministic 32 KiB incompressible payload per case. Tune with
TRACECAT_CASE_READ_BENCHMARK_CASES and TRACECAT_CASE_READ_BENCHMARK_PAYLOAD_BYTES.
The ignored output defaults to .worktrees/measurements/case-search-reads.json.
This compares requested counted/payload pages with minimal/countless pages of
the same service, isolating option costs rather than claiming a historical
production baseline. Each sample uses a fresh ORM identity map; seven warm
samples follow one excluded warmup.
"""

from __future__ import annotations

import math
import os
import random
import statistics
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import orjson
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.test_case_search_reads import (
    capture_search_queries,
    population_counts,
)
from tracecat.auth.types import Role
from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.service import CasesService
from tracecat.db.models import Case
from tracecat.pagination import CursorPaginationParams

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.usefixtures("db"),
    pytest.mark.skipif(
        os.environ.get("TRACECAT_RUN_CASE_READ_BENCHMARKS") != "1",
        reason="Set TRACECAT_RUN_CASE_READ_BENCHMARKS=1 for synthetic read measurements",
    ),
]


@dataclass(frozen=True, slots=True)
class CaseReadMeasurement:
    name: str
    include_count: bool
    include_payload: bool
    p50_ms: float
    p95_ms: float
    samples: int
    returned_rows: int
    statements_per_page: int
    population_counts_per_page: int
    payload_selected: bool
    returned_payload_bytes: int
    page_projection: str


@dataclass(frozen=True, slots=True)
class CaseReadBenchmarkReport:
    postgres_version: str
    synthetic_cases: int
    payload_bytes_per_case: int
    measurements: list[CaseReadMeasurement]
    limitations: str


async def test_large_payload_page_measurements(
    session: AsyncSession, svc_role: Role
) -> None:
    assert svc_role.workspace_id is not None
    count = int(os.environ.get("TRACECAT_CASE_READ_BENCHMARK_CASES") or 1000)
    payload_bytes = int(
        os.environ.get("TRACECAT_CASE_READ_BENCHMARK_PAYLOAD_BYTES") or 32768
    )
    assert 100 <= count <= 5000
    assert 1024 <= payload_bytes <= 262144
    generator = random.Random(2014)
    payload = generator.randbytes(math.ceil(payload_bytes / 2)).hex()[:payload_bytes]
    cases = [
        Case(
            id=uuid.uuid4(),
            workspace_id=svc_role.workspace_id,
            case_number=number,
            created_at=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(seconds=number),
            summary="Selective synthetic case"
            if number % 100 == 0
            else "Broad synthetic case",
            description="Synthetic read benchmark description",
            status=CaseStatus.NEW,
            priority=CasePriority.MEDIUM,
            severity=CaseSeverity.LOW,
            payload={"synthetic_blob": payload},
        )
        for number in range(1, count + 1)
    ]
    session.add_all(cases)
    await session.flush()
    session.expunge_all()
    await session.execute(text('ANALYZE "case"'))
    postgres_version = str(await session.scalar(text("SELECT version()")))
    service = CasesService(session, svc_role)
    measurements: list[CaseReadMeasurement] = []
    for name, search_term in (("broad", "synthetic"), ("selective", "Selective")):
        expected_ids: list[uuid.UUID] | None = None
        for include_details in (True, False):
            elapsed_ms: list[float] = []
            statement_count = population_count = returned_rows = (
                returned_payload_bytes
            ) = 0
            payload_selected = False
            page_projection = ""
            for sample in range(8):
                session.expunge_all()
                with capture_search_queries(session) as queries:
                    started = time.perf_counter()
                    page = await service.search_cases(
                        CursorPaginationParams(limit=100),
                        search_term=search_term,
                        include_count=include_details,
                        include_payload=include_details,
                    )
                    elapsed = (time.perf_counter() - started) * 1000
                ids = [item.id for item in page.items]
                if expected_ids is None:
                    expected_ids = ids
                assert ids == expected_ids
                if sample:
                    elapsed_ms.append(elapsed)
                statement_count = len(queries)
                population_count = len(population_counts(queries))
                returned_rows = len(page.items)
                returned_payload_bytes = sum(
                    len(orjson.dumps(item.payload))
                    for item in page.items
                    if item.payload is not None
                )
                payload_selected = any(
                    '"case".payload' in query.statement for query in queries
                )
                page_projection = next(
                    query.statement
                    for query in queries
                    if "_tracecat_page_key_0" in query.statement
                )
                assert population_count == int(include_details)
                assert payload_selected is include_details
                assert '"case".payload' not in page_projection
                if include_details:
                    expected_total = count if name == "broad" else count // 100
                    assert page.total_estimate == expected_total
                else:
                    assert page.total_estimate is None
            ordered = sorted(elapsed_ms)
            measurements.append(
                CaseReadMeasurement(
                    name,
                    include_details,
                    include_details,
                    round(statistics.median(ordered), 3),
                    round(ordered[math.ceil(len(ordered) * 0.95) - 1], 3),
                    len(ordered),
                    returned_rows,
                    statement_count,
                    population_count,
                    payload_selected,
                    returned_payload_bytes,
                    page_projection,
                )
            )
    report = CaseReadBenchmarkReport(
        postgres_version,
        count,
        payload_bytes,
        measurements,
        "Warm local measurements of requested read options, without concurrent load or a production latency claim. Counted/payload and minimal/countless requests intentionally have different optional metadata. The first page projection always omits payload; requested payload is batched only for returned IDs.",
    )
    output = Path(
        os.environ.get("TRACECAT_CASE_READ_BENCHMARK_OUTPUT")
        or ".worktrees/measurements/case-search-reads.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(
        orjson.dumps(
            report, option=orjson.OPT_INDENT_2 | orjson.OPT_SERIALIZE_DATACLASS
        )
    )
    print(f"Synthetic case-page measurements: {output.resolve()}")
