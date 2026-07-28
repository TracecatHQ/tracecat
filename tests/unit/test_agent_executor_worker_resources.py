from __future__ import annotations

import asyncio
import errno
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from tracecat.agent.sandbox.cgroup import CgroupAvailability


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
        *,
        reserve_mb: int,
        sandbox_memory_mb: int,
    ) -> int:
        del reserve_mb, sandbox_memory_mb
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
        lambda: CgroupAvailability.DISABLED,
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
