"""Opt-in benchmarks for case duration recomputation under burst updates.

Run with:

    TRACECAT_RUN_CASE_DURATION_BENCHMARKS=1 \
    uv run pytest tests/integration/test_case_duration_benchmarks.py -s

Tune the synthetic load with:

    TRACECAT_CASE_DURATION_BENCHMARK_CASES
    TRACECAT_CASE_DURATION_BENCHMARK_DEFINITIONS
    TRACECAT_CASE_DURATION_BENCHMARK_HISTORY_EVENTS
    TRACECAT_CASE_DURATION_BENCHMARK_UPDATES_PER_CASE
    TRACECAT_CASE_DURATION_BENCHMARK_HEALTH_INTERVAL_MS
    TRACECAT_CASE_DURATION_BENCHMARK_HEALTH_TIMEOUT_MS
    TRACECAT_CASE_DURATION_BENCHMARK_OUTPUT
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import statistics
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG
from tracecat import config
from tracecat.api.app import app
from tracecat.auth.types import Role
from tracecat.authz.scopes import ADMIN_SCOPES
from tracecat.cases.durations.schemas import (
    CaseDurationAnchorSelection,
    CaseDurationDefinitionCreate,
    CaseDurationEventAnchor,
    CaseDurationEventFilters,
)
from tracecat.cases.durations.service import (
    CaseDurationDefinitionService,
    CaseDurationService,
)
from tracecat.cases.enums import CaseEventType, CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.schemas import CaseCreate, CaseUpdate
from tracecat.cases.service import CasesService
from tracecat.db.models import CaseEvent, Organization, Workspace

RUN_BENCHMARKS = os.environ.get("TRACECAT_RUN_CASE_DURATION_BENCHMARKS") == "1"
BENCHMARK_OUTPUT_PATH = os.environ.get("TRACECAT_CASE_DURATION_BENCHMARK_OUTPUT")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.usefixtures("db"),
    pytest.mark.skipif(
        not RUN_BENCHMARKS,
        reason="Set TRACECAT_RUN_CASE_DURATION_BENCHMARKS=1 to run benchmarks",
    ),
]


@dataclass(frozen=True)
class CaseDurationBurstBenchmarkConfig:
    """Config for the synthetic duration benchmark."""

    case_count: int = int(
        os.environ.get("TRACECAT_CASE_DURATION_BENCHMARK_CASES") or 20
    )
    definition_count: int = int(
        os.environ.get("TRACECAT_CASE_DURATION_BENCHMARK_DEFINITIONS") or 80
    )
    history_events_per_case: int = int(
        os.environ.get("TRACECAT_CASE_DURATION_BENCHMARK_HISTORY_EVENTS") or 600
    )
    updates_per_case: int = int(
        os.environ.get("TRACECAT_CASE_DURATION_BENCHMARK_UPDATES_PER_CASE") or 1
    )
    health_interval_s: float = (
        int(os.environ.get("TRACECAT_CASE_DURATION_BENCHMARK_HEALTH_INTERVAL_MS") or 50)
        / 1000
    )
    health_timeout_s: float = (
        int(
            os.environ.get("TRACECAT_CASE_DURATION_BENCHMARK_HEALTH_TIMEOUT_MS") or 1000
        )
        / 1000
    )


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * percentile) - 1))
    return ordered[index]


def _latency_stats(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {
            "samples": 0,
            "p50_ms": None,
            "p95_ms": None,
            "max_ms": None,
        }
    return {
        "samples": len(values),
        "p50_ms": round(statistics.median(values) * 1000, 1),
        "p95_ms": round((_percentile(values, 0.95) or 0) * 1000, 1),
        "max_ms": round(max(values) * 1000, 1),
    }


def test_percentile_uses_ceil_rank_for_tail_latency() -> None:
    assert _percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.95) == 5.0


def test_latency_stats_reports_small_sample_p95_tail() -> None:
    assert _latency_stats([0.001, 0.002, 0.003, 0.004, 0.005])["p95_ms"] == 5.0


def _write_summary_to_file(summary: dict[str, object]) -> None:
    if not BENCHMARK_OUTPUT_PATH:
        return
    output_path = os.path.abspath(BENCHMARK_OUTPUT_PATH)
    if parent := os.path.dirname(output_path):
        os.makedirs(parent, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)


async def _seed_cases_definitions_and_history(
    *,
    async_engine,
    role: Role,
    cfg: CaseDurationBurstBenchmarkConfig,
) -> list[uuid.UUID]:
    case_ids: list[uuid.UUID] = []
    async with AsyncSession(async_engine, expire_on_commit=False) as session:
        cases_service = CasesService(session=session, role=role)
        for index in range(cfg.case_count):
            case = await cases_service.create_case(
                CaseCreate(
                    summary=f"Duration benchmark case {index}",
                    description="Synthetic benchmark case",
                    status=CaseStatus.NEW,
                    priority=CasePriority.MEDIUM,
                    severity=CaseSeverity.MEDIUM,
                )
            )
            case_ids.append(case.id)

        definition_service = CaseDurationDefinitionService(session=session, role=role)
        for index in range(cfg.definition_count):
            await definition_service.create_definition(
                CaseDurationDefinitionCreate(
                    name=f"Benchmark Duration {index}",
                    description="Synthetic duration benchmark definition",
                    start_anchor=CaseDurationEventAnchor(
                        event_type=CaseEventType.CASE_CREATED,
                    ),
                    end_anchor=CaseDurationEventAnchor(
                        event_type=CaseEventType.STATUS_CHANGED,
                        filters=CaseDurationEventFilters(
                            new_values=[
                                CaseStatus.IN_PROGRESS.value,
                                CaseStatus.RESOLVED.value,
                                CaseStatus.CLOSED.value,
                            ]
                        ),
                        selection=CaseDurationAnchorSelection.LAST,
                    ),
                )
            )

        base_time = datetime.now(UTC) - timedelta(hours=1)
        for case_index, case_id in enumerate(case_ids):
            current_status = CaseStatus.NEW
            for event_index in range(cfg.history_events_per_case):
                new_status = (
                    CaseStatus.IN_PROGRESS
                    if event_index % 3 == 0
                    else CaseStatus.RESOLVED
                    if event_index % 3 == 1
                    else CaseStatus.CLOSED
                )
                session.add(
                    CaseEvent(
                        workspace_id=role.workspace_id,
                        case_id=case_id,
                        type=CaseEventType.STATUS_CHANGED,
                        data={
                            "old": current_status.value,
                            "new": new_status.value,
                        },
                        user_id=role.user_id,
                        created_at=base_time
                        + timedelta(
                            milliseconds=(
                                case_index * cfg.history_events_per_case + event_index
                            )
                        ),
                    )
                )
                current_status = new_status
        await session.commit()

    return case_ids


async def _seed_benchmark_role(async_engine) -> Role:
    organization_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    async with AsyncSession(async_engine, expire_on_commit=False) as session:
        session.add(
            Organization(
                id=organization_id,
                name="Case Duration Benchmark Org",
                slug=f"case-duration-benchmark-{organization_id.hex[:8]}",
                is_active=True,
            )
        )
        session.add(
            Workspace(
                id=workspace_id,
                organization_id=organization_id,
                name="Case Duration Benchmark Workspace",
                last_case_number=0,
            )
        )
        await session.commit()

    return Role(
        type="user",
        workspace_id=workspace_id,
        organization_id=organization_id,
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
        scopes=ADMIN_SCOPES,
    )


async def _run_case_update_burst(
    *,
    async_engine,
    role: Role,
    case_ids: list[uuid.UUID],
    updates_per_case: int,
) -> tuple[list[float], int]:
    async def update_one_case(
        case_id: uuid.UUID, worker_index: int
    ) -> tuple[list[float], int]:
        latencies: list[float] = []
        errors = 0
        async with AsyncSession(async_engine, expire_on_commit=False) as session:
            service = CasesService(session=session, role=role)
            for update_index in range(updates_per_case):
                case = await service.get_case(case_id, for_update=True)
                if case is None:
                    raise AssertionError(f"Case {case_id} not found during benchmark")
                started = time.perf_counter()
                try:
                    await service.update_case(
                        case,
                        CaseUpdate(
                            summary=(
                                "duration-burst-"
                                f"{worker_index}-{update_index}-{uuid.uuid4().hex[:8]}"
                            )
                        ),
                    )
                except Exception:
                    errors += 1
                    await session.rollback()
                else:
                    latencies.append(time.perf_counter() - started)
        return latencies, errors

    worker_results = await asyncio.gather(
        *[
            update_one_case(case_id, worker_index)
            for worker_index, case_id in enumerate(case_ids)
        ]
    )
    return (
        [latency for values, _ in worker_results for latency in values],
        sum(errors for _, errors in worker_results),
    )


@pytest.mark.anyio
async def test_case_duration_update_burst_health_latency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Measure health latency during a burst of case updates with duration sync."""

    cfg = CaseDurationBurstBenchmarkConfig()
    monkeypatch.setattr(config, "TRACECAT__CASE_TRIGGERS_ENABLED", False)

    async_engine = create_async_engine(
        TEST_DB_CONFIG.test_url,
        poolclass=NullPool,
    )

    phase = {"name": "baseline"}
    stop_probe = asyncio.Event()
    health_latencies: dict[str, list[float]] = {
        "baseline": [],
        "burst": [],
        "cooldown": [],
    }
    loop_lags: dict[str, list[float]] = {
        "baseline": [],
        "burst": [],
        "cooldown": [],
    }
    health_errors: dict[str, int] = {"baseline": 0, "burst": 0, "cooldown": 0}

    async def probe_health() -> None:
        next_tick = time.perf_counter() + cfg.health_interval_s
        transport = httpx.ASGITransport(app=app)
        async with (
            transport,
            httpx.AsyncClient(
                transport=transport, base_url="http://benchmark"
            ) as client,
        ):
            while not stop_probe.is_set():
                await asyncio.sleep(max(0, next_tick - time.perf_counter()))
                scheduled = next_tick
                current_phase = phase["name"]
                started = time.perf_counter()
                try:
                    response = await asyncio.wait_for(
                        client.get("/health"),
                        timeout=cfg.health_timeout_s,
                    )
                    response.raise_for_status()
                    health_latencies[current_phase].append(
                        time.perf_counter() - started
                    )
                except Exception:
                    health_errors[current_phase] += 1
                loop_lags[current_phase].append(time.perf_counter() - scheduled)
                next_tick += cfg.health_interval_s

    try:
        with (
            patch.object(
                CaseDurationDefinitionService,
                "has_entitlement",
                new=AsyncMock(return_value=True),
            ),
            patch.object(
                CaseDurationService,
                "has_entitlement",
                new=AsyncMock(return_value=True),
            ),
            patch.object(
                CasesService,
                "has_entitlement",
                new=AsyncMock(return_value=False),
            ),
        ):
            role = await _seed_benchmark_role(async_engine)
            case_ids = await _seed_cases_definitions_and_history(
                async_engine=async_engine,
                role=role,
                cfg=cfg,
            )

            probe_task = asyncio.create_task(probe_health())
            await asyncio.sleep(0.25)

            phase["name"] = "burst"
            burst_started = time.perf_counter()
            update_latencies, update_errors = await _run_case_update_burst(
                async_engine=async_engine,
                role=role,
                case_ids=case_ids,
                updates_per_case=cfg.updates_per_case,
            )
            burst_elapsed = time.perf_counter() - burst_started

            phase["name"] = "cooldown"
            await asyncio.sleep(0.25)
            stop_probe.set()
            await probe_task

        summary: dict[str, object] = {
            "config": {
                "cases": cfg.case_count,
                "definitions": cfg.definition_count,
                "history_events_per_case": cfg.history_events_per_case,
                "updates_per_case": cfg.updates_per_case,
                "health_interval_ms": round(cfg.health_interval_s * 1000),
                "health_timeout_ms": round(cfg.health_timeout_s * 1000),
            },
            "burst_elapsed_s": round(burst_elapsed, 3),
            "update_latencies": _latency_stats(update_latencies),
            "update_errors": update_errors,
            "health_baseline": _latency_stats(health_latencies["baseline"]),
            "health_burst": _latency_stats(health_latencies["burst"]),
            "health_cooldown": _latency_stats(health_latencies["cooldown"]),
            "loop_lag_baseline": _latency_stats(loop_lags["baseline"]),
            "loop_lag_burst": _latency_stats(loop_lags["burst"]),
            "loop_lag_cooldown": _latency_stats(loop_lags["cooldown"]),
            "health_errors": health_errors,
        }
        _write_summary_to_file(summary)

        print("\nCase duration burst benchmark:")
        print(summary)

        attempted_updates = cfg.case_count * cfg.updates_per_case
        assert len(update_latencies) + update_errors == attempted_updates
        assert update_latencies
        assert health_latencies["baseline"]
        assert health_latencies["burst"] or health_errors["burst"] > 0
    finally:
        await async_engine.dispose()
