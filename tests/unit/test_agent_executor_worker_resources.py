from __future__ import annotations

import asyncio
import errno
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from tracecat.agent.sandbox.cgroup import (
    AgentExecutorMemoryBudgetError,
    AgentSandboxCgroupUnavailableError,
    CgroupAvailability,
    PreparedCgroup,
)


@pytest.mark.anyio
async def test_agent_executor_readiness_sentinel_exists_only_while_running(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tracecat.agent import executor_worker

    ready_file = tmp_path / "run" / "agent-executor-ready"
    shutdown_event = asyncio.Event()
    observed_contents: list[str] = []

    class _FakeWorker:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        async def __aenter__(self) -> _FakeWorker:
            return self

        async def __aexit__(
            self,
            exc_type: object,
            exc: object,
            tb: object,
        ) -> None:
            del exc_type, exc, tb

    def keep_concurrency(
        max_concurrent: int,
        prepared_cgroup: PreparedCgroup,
        *,
        reserve_mb: int,
        sandbox_memory_mb: int,
    ) -> int:
        del prepared_cgroup, reserve_mb, sandbox_memory_mb
        return max_concurrent

    async def observe_readiness() -> None:
        try:
            for _ in range(1000):
                if ready_file.exists():
                    observed_contents.append(ready_file.read_text().strip())
                    return
                await asyncio.sleep(0)
            pytest.fail("readiness sentinel was not created")
        finally:
            shutdown_event.set()

    monkeypatch.setenv("TRACECAT__AGENT_EXECUTOR_MAX_CONCURRENT_ACTIVITIES", "2")
    monkeypatch.setattr(
        executor_worker,
        "prepare_agent_sandbox_cgroup",
        lambda *, enabled: PreparedCgroup(CgroupAvailability.DISABLED, None),
    )
    monkeypatch.setattr(
        executor_worker,
        "clamp_agent_executor_concurrency",
        keep_concurrency,
    )
    monkeypatch.setattr(
        executor_worker,
        "_start_runtime_services",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(executor_worker, "_stop_runtime_services", AsyncMock())
    monkeypatch.setattr(executor_worker, "close_storage_client_cache", AsyncMock())
    monkeypatch.setattr(executor_worker, "Worker", _FakeWorker)
    monkeypatch.setattr(executor_worker, "new_sandbox_runner", lambda: object())
    monkeypatch.setattr(
        executor_worker.config,
        "TRACECAT__AGENT_EXECUTOR_READY_FILE",
        str(ready_file),
    )

    observer = asyncio.create_task(observe_readiness())
    await asyncio.wait_for(
        executor_worker.main(shutdown_event=shutdown_event),
        timeout=2,
    )
    await observer

    assert not ready_file.exists()
    assert len(observed_contents) == 1
    started_at = datetime.fromisoformat(observed_contents[0])
    assert started_at.tzinfo is UTC


def test_agent_executor_readiness_sentinel_is_best_effort_on_read_only_fs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tracecat.agent import executor_worker

    mock_logger = Mock()
    monkeypatch.setattr(executor_worker, "logger", mock_logger)

    def deny_mkdir(
        self: Path,
        mode: int = 0o777,
        parents: bool = False,
        exist_ok: bool = False,
    ) -> None:
        del self, mode, parents, exist_ok
        raise PermissionError(errno.EROFS, "read-only filesystem")

    monkeypatch.setattr(Path, "mkdir", deny_mkdir)

    created = executor_worker._write_readiness_file(
        tmp_path / "run" / "agent-executor-ready",
        datetime(2026, 7, 27, tzinfo=UTC),
    )

    assert created is False
    mock_logger.warning.assert_called_once()
    assert mock_logger.warning.call_args.kwargs["errno"] == errno.EROFS


@pytest.mark.anyio
async def test_agent_executor_removes_stale_readiness_sentinel_on_startup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tracecat.agent import executor_worker

    ready_file = tmp_path / "run" / "agent-executor-ready"
    ready_file.parent.mkdir(parents=True)
    ready_file.write_text("stale\n")

    def keep_concurrency(
        max_concurrent: int,
        prepared_cgroup: PreparedCgroup,
        *,
        reserve_mb: int,
        sandbox_memory_mb: int,
    ) -> int:
        del prepared_cgroup, reserve_mb, sandbox_memory_mb
        return max_concurrent

    monkeypatch.setenv("TRACECAT__AGENT_EXECUTOR_MAX_CONCURRENT_ACTIVITIES", "1")
    monkeypatch.setattr(
        executor_worker,
        "prepare_agent_sandbox_cgroup",
        lambda *, enabled: PreparedCgroup(CgroupAvailability.DISABLED, None),
    )
    monkeypatch.setattr(
        executor_worker,
        "clamp_agent_executor_concurrency",
        keep_concurrency,
    )
    monkeypatch.setattr(
        executor_worker,
        "_start_runtime_services",
        AsyncMock(side_effect=RuntimeError("startup failed")),
    )
    monkeypatch.setattr(executor_worker, "_stop_runtime_services", AsyncMock())
    monkeypatch.setattr(executor_worker, "close_storage_client_cache", AsyncMock())
    monkeypatch.setattr(
        executor_worker.config,
        "TRACECAT__AGENT_EXECUTOR_READY_FILE",
        str(ready_file),
    )

    with pytest.raises(RuntimeError, match="startup failed"):
        await executor_worker.main(shutdown_event=asyncio.Event())

    assert not ready_file.exists()


@pytest.mark.anyio
async def test_agent_executor_memory_budget_error_propagates_from_main(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tracecat.agent import executor_worker

    cgroup_root = tmp_path / "cgroup"
    cgroup_root.mkdir()
    (cgroup_root / "memory.max").write_text(f"{4096 * 1024 * 1024}\n")
    start_runtime_services = AsyncMock()
    monkeypatch.setenv("TRACECAT__AGENT_EXECUTOR_MAX_CONCURRENT_ACTIVITIES", "1")
    monkeypatch.setattr(
        executor_worker.config,
        "TRACECAT__AGENT_EXECUTOR_MEMORY_RESERVE_MB",
        4096,
    )
    monkeypatch.setattr(
        executor_worker.config,
        "TRACECAT__AGENT_SANDBOX_MEMORY_MB",
        4096,
    )
    monkeypatch.setattr(
        executor_worker,
        "prepare_agent_sandbox_cgroup",
        lambda *, enabled: PreparedCgroup(
            CgroupAvailability.UNAVAILABLE,
            cgroup_root,
        ),
    )
    monkeypatch.setattr(
        executor_worker,
        "_start_runtime_services",
        start_runtime_services,
    )

    with pytest.raises(
        AgentExecutorMemoryBudgetError,
        match="container_limit_mb=4096",
    ):
        await executor_worker.main(shutdown_event=asyncio.Event())

    start_runtime_services.assert_not_awaited()


@pytest.mark.anyio
async def test_agent_executor_clears_stale_sentinel_before_budget_validation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tracecat.agent import executor_worker

    ready_file = tmp_path / "run" / "agent-executor-ready"
    ready_file.parent.mkdir(parents=True)
    ready_file.write_text("stale\n")

    cgroup_root = tmp_path / "cgroup"
    cgroup_root.mkdir()
    (cgroup_root / "memory.max").write_text(f"{4096 * 1024 * 1024}\n")
    monkeypatch.setenv("TRACECAT__AGENT_EXECUTOR_MAX_CONCURRENT_ACTIVITIES", "1")
    monkeypatch.setattr(
        executor_worker.config,
        "TRACECAT__AGENT_EXECUTOR_MEMORY_RESERVE_MB",
        4096,
    )
    monkeypatch.setattr(
        executor_worker.config,
        "TRACECAT__AGENT_SANDBOX_MEMORY_MB",
        4096,
    )
    monkeypatch.setattr(
        executor_worker,
        "prepare_agent_sandbox_cgroup",
        lambda *, enabled: PreparedCgroup(
            CgroupAvailability.UNAVAILABLE,
            cgroup_root,
        ),
    )
    monkeypatch.setattr(
        executor_worker.config,
        "TRACECAT__AGENT_EXECUTOR_READY_FILE",
        str(ready_file),
    )

    with pytest.raises(AgentExecutorMemoryBudgetError):
        await executor_worker.main(shutdown_event=asyncio.Event())

    assert not ready_file.exists()


@pytest.mark.anyio
async def test_agent_executor_fails_closed_when_required_cgroup_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tracecat.agent import executor_worker

    ready_file = tmp_path / "run" / "agent-executor-ready"
    prepare_cgroup = Mock(
        return_value=PreparedCgroup(CgroupAvailability.UNAVAILABLE, None)
    )
    start_runtime_services = AsyncMock()
    monkeypatch.setenv("TRACECAT__AGENT_EXECUTOR_MAX_CONCURRENT_ACTIVITIES", "1")
    monkeypatch.setattr(
        executor_worker.config,
        "TRACECAT__AGENT_SANDBOX_CGROUP_ENABLED",
        True,
    )
    monkeypatch.setattr(
        executor_worker.config,
        "TRACECAT__DISABLE_NSJAIL",
        False,
    )
    monkeypatch.setattr(
        executor_worker.config,
        "TRACECAT__AGENT_EXECUTOR_READY_FILE",
        str(ready_file),
    )
    monkeypatch.setattr(
        executor_worker,
        "prepare_agent_sandbox_cgroup",
        prepare_cgroup,
    )
    monkeypatch.setattr(
        executor_worker,
        "_start_runtime_services",
        start_runtime_services,
    )

    with pytest.raises(AgentSandboxCgroupUnavailableError):
        await executor_worker.main(shutdown_event=asyncio.Event())

    prepare_cgroup.assert_called_once_with(enabled=True)
    start_runtime_services.assert_not_awaited()
    assert not ready_file.exists()


@pytest.mark.anyio
async def test_agent_executor_disables_cgroups_when_nsjail_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tracecat.agent import executor_worker

    prepare_cgroup = Mock(
        return_value=PreparedCgroup(CgroupAvailability.DISABLED, None)
    )
    start_runtime_services = AsyncMock(side_effect=RuntimeError("stop after setup"))
    monkeypatch.setenv("TRACECAT__AGENT_EXECUTOR_MAX_CONCURRENT_ACTIVITIES", "1")
    monkeypatch.setattr(
        executor_worker.config,
        "TRACECAT__AGENT_SANDBOX_CGROUP_ENABLED",
        True,
    )
    monkeypatch.setattr(
        executor_worker.config,
        "TRACECAT__DISABLE_NSJAIL",
        True,
    )
    monkeypatch.setattr(
        executor_worker,
        "prepare_agent_sandbox_cgroup",
        prepare_cgroup,
    )
    monkeypatch.setattr(
        executor_worker,
        "_start_runtime_services",
        start_runtime_services,
    )
    monkeypatch.setattr(executor_worker, "_stop_runtime_services", AsyncMock())
    monkeypatch.setattr(executor_worker, "close_storage_client_cache", AsyncMock())

    with pytest.raises(RuntimeError, match="stop after setup"):
        await executor_worker.main(shutdown_event=asyncio.Event())

    prepare_cgroup.assert_called_once_with(enabled=False)


@pytest.mark.anyio
@pytest.mark.parametrize("max_concurrent", ["0", "-1"])
async def test_agent_executor_rejects_nonpositive_concurrency(
    monkeypatch: pytest.MonkeyPatch,
    max_concurrent: str,
) -> None:
    from tracecat.agent import executor_worker

    prepare_cgroup = Mock()
    monkeypatch.setenv(
        "TRACECAT__AGENT_EXECUTOR_MAX_CONCURRENT_ACTIVITIES",
        max_concurrent,
    )
    monkeypatch.setattr(
        executor_worker,
        "prepare_agent_sandbox_cgroup",
        prepare_cgroup,
    )

    with pytest.raises(
        ValueError,
        match="TRACECAT__AGENT_EXECUTOR_MAX_CONCURRENT_ACTIVITIES",
    ):
        await executor_worker.main(shutdown_event=asyncio.Event())

    prepare_cgroup.assert_not_called()
