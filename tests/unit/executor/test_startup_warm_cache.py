from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from tracecat import config
from tracecat.executor import startup_warm_cache


@pytest.mark.anyio
async def test_warm_registry_cache_on_startup_skips_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__EXECUTOR_WARM_CACHE_ENABLED", False)
    monkeypatch.setattr(config, "TRACECAT__LOCAL_REPOSITORY_ENABLED", False)

    report = await startup_warm_cache.warm_registry_cache_on_startup()

    assert report.enabled is False
    assert report.skipped_reason == "warmup_disabled"


@pytest.mark.anyio
async def test_warm_registry_cache_on_startup_skips_when_local_repository_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__EXECUTOR_WARM_CACHE_ENABLED", True)
    monkeypatch.setattr(config, "TRACECAT__LOCAL_REPOSITORY_ENABLED", True)

    report = await startup_warm_cache.warm_registry_cache_on_startup()

    assert report.enabled is False
    assert report.skipped_reason == "local_repository_enabled"


@pytest.mark.anyio
async def test_warm_registry_cache_on_startup_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__EXECUTOR_WARM_CACHE_ENABLED", True)
    monkeypatch.setattr(config, "TRACECAT__LOCAL_REPOSITORY_ENABLED", False)
    monkeypatch.setattr(config, "TRACECAT__EXECUTOR_WARM_CACHE_TIMEOUT_SECONDS", 1)

    async def _slow_run_warmup() -> startup_warm_cache.WarmCacheReport:
        await asyncio.sleep(2)
        return startup_warm_cache.WarmCacheReport(enabled=True)

    monkeypatch.setattr(startup_warm_cache, "_run_warmup", _slow_run_warmup)

    report = await startup_warm_cache.warm_registry_cache_on_startup()

    assert report.enabled is True
    assert report.timed_out is True


@pytest.mark.anyio
async def test_warm_registry_cache_on_startup_handles_unexpected_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__EXECUTOR_WARM_CACHE_ENABLED", True)
    monkeypatch.setattr(config, "TRACECAT__LOCAL_REPOSITORY_ENABLED", False)
    monkeypatch.setattr(config, "TRACECAT__EXECUTOR_WARM_CACHE_TIMEOUT_SECONDS", 5)

    async def _boom() -> startup_warm_cache.WarmCacheReport:
        raise RuntimeError("boom")

    monkeypatch.setattr(startup_warm_cache, "_run_warmup", _boom)

    report = await startup_warm_cache.warm_registry_cache_on_startup()

    assert report.enabled is True
    assert report.skipped_reason == "unexpected_error"


@pytest.mark.anyio
async def test_run_warmup_merges_published_and_scheduled_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warmed_inputs: list[str] = []

    async def _published() -> list[str]:
        return ["1.0.0"]

    async def _scheduled() -> list[str]:
        return ["1.0.0", "2.0.0"]

    async def _definition_uris(versions: list[str]) -> set[str]:
        assert versions == ["1.0.0", "2.0.0"]
        return {"s3://tracecat/1.tar.gz", "s3://tracecat/2.tar.gz"}

    async def _platform_uris() -> set[str]:
        return {"s3://tracecat/platform.tar.gz"}

    async def _warm(uris: list[str]) -> tuple[int, int]:
        warmed_inputs.extend(uris)
        return len(uris), 0

    monkeypatch.setattr(
        startup_warm_cache,
        "_collect_published_platform_lock_versions",
        _published,
    )
    monkeypatch.setattr(
        startup_warm_cache,
        "_collect_online_schedule_platform_lock_versions",
        _scheduled,
    )
    monkeypatch.setattr(
        startup_warm_cache, "_resolve_definition_tarball_uris", _definition_uris
    )
    monkeypatch.setattr(
        startup_warm_cache, "_collect_platform_current_tarball_uris", _platform_uris
    )
    monkeypatch.setattr(startup_warm_cache, "_warm_tarball_uris", _warm)
    monkeypatch.setattr(config, "TRACECAT__EXECUTOR_WARM_CACHE_MAX_TARBALLS", 2)

    report = await startup_warm_cache._run_warmup()

    assert report.enabled is True
    assert report.published_definition_rows == 1
    assert report.scheduled_definition_rows == 2
    assert report.definition_rows == 2
    assert report.definition_locks == 2
    assert report.platform_tarballs == 1
    assert report.candidate_tarballs == 2
    assert report.warmed_tarballs == 2
    assert report.failed_tarballs == 0
    assert sorted(warmed_inputs) == [
        "s3://tracecat/1.tar.gz",
        "s3://tracecat/2.tar.gz",
    ]


class _FakeTuplesResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[object, ...]]:
        return self._rows

    def __iter__(self) -> Iterator[tuple[object, ...]]:
        return iter(self._rows)


class _FakeExecuteResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows

    def tuples(self) -> _FakeTuplesResult:
        return _FakeTuplesResult(self._rows)


@pytest.mark.anyio
async def test_collect_online_schedule_platform_lock_versions_filters_missing_platform_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows: list[tuple[object, ...]] = [
        ("1.0.0",),
        (None,),
        ("2.0.0",),
    ]

    class _FakeSession:
        async def execute(self, stmt: Any) -> _FakeExecuteResult:
            # Force SQL compilation so ambiguous ORM joins fail in unit tests.
            stmt.compile(dialect=postgresql.dialect())
            return _FakeExecuteResult(rows)

    @asynccontextmanager
    async def _session_cm() -> AsyncIterator[_FakeSession]:
        yield _FakeSession()

    monkeypatch.setattr(
        startup_warm_cache, "get_async_session_context_manager", _session_cm
    )

    versions = (
        await startup_warm_cache._collect_online_schedule_platform_lock_versions()
    )

    assert versions == ["1.0.0", "2.0.0"]


@pytest.mark.anyio
async def test_run_warmup_dedupes_versions_before_definition_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_versions: list[str] = []

    async def _published() -> list[str]:
        return []

    async def _scheduled() -> list[str]:
        # Duplicate locked version plus one distinct locked version.
        return ["1.0.0", "1.0.0", "2.0.0"]

    async def _definition_uris(versions: list[str]) -> set[str]:
        seen_versions.extend(versions)
        return set()

    async def _platform_uris() -> set[str]:
        return set()

    async def _warm(_uris: list[str]) -> tuple[int, int]:
        return 0, 0

    monkeypatch.setattr(
        startup_warm_cache,
        "_collect_published_platform_lock_versions",
        _published,
    )
    monkeypatch.setattr(
        startup_warm_cache,
        "_collect_online_schedule_platform_lock_versions",
        _scheduled,
    )
    monkeypatch.setattr(
        startup_warm_cache, "_resolve_definition_tarball_uris", _definition_uris
    )
    monkeypatch.setattr(
        startup_warm_cache, "_collect_platform_current_tarball_uris", _platform_uris
    )
    monkeypatch.setattr(startup_warm_cache, "_warm_tarball_uris", _warm)
    monkeypatch.setattr(config, "TRACECAT__EXECUTOR_WARM_CACHE_MAX_LOCKED_VERSIONS", 2)

    report = await startup_warm_cache._run_warmup()

    assert report.published_definition_rows == 0
    assert report.scheduled_definition_rows == 3
    assert report.definition_rows == 2
    assert seen_versions == ["1.0.0", "2.0.0"]


@pytest.mark.anyio
async def test_resolve_definition_tarball_uris_dedupes_versions_and_skips_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows: list[tuple[object, ...]] = [
        ("s3://tracecat/a.tar.gz",),
        ("s3://tracecat/b.tar.gz",),
        ("s3://tracecat/a.tar.gz",),
        (None,),
    ]

    class _FakeSession:
        async def execute(self, stmt: Any) -> _FakeExecuteResult:
            stmt.compile(dialect=postgresql.dialect())
            return _FakeExecuteResult(rows)

    @asynccontextmanager
    async def _session_cm() -> AsyncIterator[_FakeSession]:
        yield _FakeSession()

    monkeypatch.setattr(
        startup_warm_cache, "get_async_session_context_manager", _session_cm
    )

    uris = await startup_warm_cache._resolve_definition_tarball_uris(
        ["1.0.0", "1.0.0", "2.0.0"]
    )

    assert uris == {"s3://tracecat/a.tar.gz", "s3://tracecat/b.tar.gz"}


@pytest.mark.anyio
async def test_collect_platform_current_tarball_uris_dedupes_and_skips_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows: list[tuple[object, ...]] = [
        ("repo-a", "s3://tracecat/a.tar.gz"),
        ("repo-b", "s3://tracecat/b.tar.gz"),
        ("repo-c", "s3://tracecat/a.tar.gz"),
        ("repo-d", None),
    ]

    class _FakeSession:
        async def execute(self, _stmt: object) -> _FakeExecuteResult:
            return _FakeExecuteResult(rows)

    @asynccontextmanager
    async def _session_cm() -> AsyncIterator[_FakeSession]:
        yield _FakeSession()

    monkeypatch.setattr(
        startup_warm_cache, "get_async_session_context_manager", _session_cm
    )

    uris = await startup_warm_cache._collect_platform_current_tarball_uris()

    assert uris == {"s3://tracecat/a.tar.gz", "s3://tracecat/b.tar.gz"}
