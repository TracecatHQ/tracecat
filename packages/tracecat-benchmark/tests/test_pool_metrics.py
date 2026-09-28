from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import sqlalchemy.ext.asyncio as sqlalchemy_asyncio
from sqlalchemy.util.concurrency import greenlet_spawn
from tracecat_benchmark import pool_metrics
from tracecat_benchmark.pool_metrics import (
    InstrumentedAsyncAdaptedQueuePool,
    PoolMetricsSnapshot,
    _create_instrumented_async_engine,
    install_pool_metrics_instrumentation,
    pool_metrics_document,
)


def test_instrumented_engine_preserves_pool_settings_and_detects_auth_pool() -> None:
    async def exercise() -> None:
        engine = _create_instrumented_async_engine(
            "postgresql+asyncpg://postgres:postgres@localhost/postgres",
            pool_size=3,
            max_overflow=2,
            pool_timeout=7,
            connect_args={
                "server_settings": {"application_name": "tracecat-worker-auth"}
            },
        )
        try:
            assert isinstance(engine.pool, InstrumentedAsyncAdaptedQueuePool)
            auth_pool = next(
                pool
                for pool in pool_metrics_document()["pools"]
                if pool["pool"] == "auth"
            )
            assert auth_pool["configured_size"] == 3
            assert auth_pool["configured_max_overflow"] == 2
            assert auth_pool["configured_timeout_seconds"] == 7
        finally:
            await engine.dispose()

    asyncio.run(exercise())


def test_physical_reconnect_is_recorded_on_the_checkout_that_opens_it() -> None:
    class FakeConnection:
        def rollback(self) -> None:
            pass

        def close(self) -> None:
            pass

    pool = InstrumentedAsyncAdaptedQueuePool(
        lambda: FakeConnection(),
        pool_size=1,
        max_overflow=0,
        pool_metrics_name="reconnect-test",
    )

    async def exercise() -> tuple[PoolMetricsSnapshot, PoolMetricsSnapshot]:
        first = await greenlet_spawn(pool.connect)
        await greenlet_spawn(first.close)
        pool._invalidate_time = time.time() + 1

        second = await greenlet_spawn(pool.connect)
        await greenlet_spawn(second.close)
        after_reconnect = next(
            item
            for item in pool_metrics_document()["pools"]
            if item["pool"] == "reconnect-test"
        )
        pool._invalidate_time = 0

        third = await greenlet_spawn(pool.connect)
        await greenlet_spawn(third.close)
        final = next(
            item
            for item in pool_metrics_document()["pools"]
            if item["pool"] == "reconnect-test"
        )
        await greenlet_spawn(pool.dispose)
        return after_reconnect, final

    after_reconnect, final = asyncio.run(exercise())

    assert after_reconnect["connections_created_total"] == 2
    assert after_reconnect["connection_open"]["count"] == 2
    assert final["connections_created_total"] == 2
    assert final["connection_open"]["count"] == 2
    assert final["checkouts_total"] == 3
    assert final["checkout_wait"]["count"] == 3


def test_sitecustomize_requires_private_benchmark_opt_in() -> None:
    package_root = Path(__file__).parents[1]
    python_path = os.pathsep.join((str(package_root / "bootstrap"), str(package_root)))
    command = [
        sys.executable,
        "-c",
        "import sqlalchemy.ext.asyncio as module; print(module.create_async_engine.__module__)",
    ]
    base_environment = os.environ.copy()
    base_environment["PYTHONPATH"] = python_path
    base_environment.pop(
        "TRACECAT_BENCHMARK_INTERNAL_DB_POOL_METRICS_PORT",
        None,
    )

    disabled = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        env=base_environment,
    )
    assert disabled.stdout.strip() == "sqlalchemy.ext.asyncio.engine"

    enabled_environment = base_environment | {
        "TRACECAT_BENCHMARK_INTERNAL_DB_POOL_METRICS_PORT": "9091"
    }
    enabled = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        env=enabled_environment,
    )
    assert enabled.stdout.strip() == "tracecat_benchmark.pool_metrics"


@pytest.mark.parametrize(
    ("service_name", "process_id", "expected_ports"),
    [
        ("worker", 1, [9091]),
        ("executor", 1, [9091]),
        ("api", 1, []),
        ("executor", 2, []),
    ],
)
def test_only_main_idle_worker_processes_start_metrics_eagerly(
    monkeypatch: pytest.MonkeyPatch,
    service_name: str,
    process_id: int,
    expected_ports: list[int],
) -> None:
    started_ports: list[int] = []
    original_create_async_engine = sqlalchemy_asyncio.create_async_engine
    monkeypatch.setenv(
        "TRACECAT_BENCHMARK_INTERNAL_DB_POOL_METRICS_PORT",
        "9091",
    )
    monkeypatch.setenv("TRACECAT__SERVICE_NAME", service_name)
    monkeypatch.setattr(pool_metrics.os, "getpid", lambda: process_id)
    monkeypatch.setattr(
        pool_metrics,
        "start_pool_metrics_server",
        started_ports.append,
    )
    monkeypatch.setattr(pool_metrics, "_instrumentation_installed", False)
    monkeypatch.setattr(pool_metrics, "_instrumentation_port", 0)
    monkeypatch.setattr(
        pool_metrics,
        "_original_create_async_engine",
        original_create_async_engine,
    )
    monkeypatch.setattr(
        sqlalchemy_asyncio,
        "create_async_engine",
        original_create_async_engine,
    )

    install_pool_metrics_instrumentation()

    assert started_ports == expected_ports
    assert sqlalchemy_asyncio.create_async_engine is not original_create_async_engine
