import asyncio
import contextlib
import uuid
from collections.abc import Awaitable, Callable
from typing import cast
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.cases.durations.sync_queue import (
    ROLLOUT_BACKFILL_LEASE_SECONDS,
    ROLLOUT_BACKFILL_MARKER_KEY,
    enqueue_case_duration_sync_after_commit,
    enqueue_rollout_backfill_once,
)


def _mock_rollout_environment(
    monkeypatch: pytest.MonkeyPatch,
    *,
    marker_acquired: bool,
    workspace_ids: list[uuid.UUID],
) -> tuple[MagicMock, AsyncMock]:
    redis_mock = MagicMock()
    redis_mock.set_if_not_exists = AsyncMock(return_value=marker_acquired)
    redis_mock.set = AsyncMock()
    redis_mock.delete = AsyncMock()
    monkeypatch.setattr(
        "tracecat.cases.durations.sync_queue.get_redis_client",
        AsyncMock(return_value=redis_mock),
    )

    session = MagicMock()
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = workspace_ids
    session.execute = AsyncMock(return_value=execute_result)

    @contextlib.asynccontextmanager
    async def fake_session_context():
        yield session

    monkeypatch.setattr(
        "tracecat.cases.durations.sync_queue.get_async_session_bypass_rls_context_manager",
        fake_session_context,
    )
    publish_mock = AsyncMock(return_value="1-0")
    monkeypatch.setattr(
        "tracecat.cases.durations.sync_queue.publish_case_duration_sync",
        publish_mock,
    )
    return redis_mock, publish_mock


@pytest.mark.anyio
async def test_rollout_backfill_publishes_once_per_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_ids = [uuid.uuid4(), uuid.uuid4()]
    redis_mock, publish_mock = _mock_rollout_environment(
        monkeypatch, marker_acquired=True, workspace_ids=workspace_ids
    )

    await enqueue_rollout_backfill_once()

    assert publish_mock.await_args_list == [
        call(workspace_id=workspace_id, reason="duration_definition_updated")
        for workspace_id in workspace_ids
    ]
    # Lease acquired with a TTL, then made permanent after all jobs queued.
    assert (
        redis_mock.set_if_not_exists.await_args.kwargs["expire_seconds"]
        == ROLLOUT_BACKFILL_LEASE_SECONDS
    )
    assert redis_mock.set.await_args.kwargs["expire_seconds"] is None


@pytest.mark.anyio
async def test_rollout_backfill_keeps_lease_on_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancelled startup pass must neither persist the marker nor delete
    the lease; the TTL lapses so a later boot retries the rollout."""
    redis_mock, publish_mock = _mock_rollout_environment(
        monkeypatch, marker_acquired=True, workspace_ids=[uuid.uuid4()]
    )
    publish_mock.side_effect = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await enqueue_rollout_backfill_once()

    redis_mock.set.assert_not_awaited()
    redis_mock.delete.assert_not_awaited()


@pytest.mark.anyio
async def test_rollout_backfill_skips_when_marker_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, publish_mock = _mock_rollout_environment(
        monkeypatch, marker_acquired=False, workspace_ids=[uuid.uuid4()]
    )

    await enqueue_rollout_backfill_once()

    publish_mock.assert_not_awaited()


@pytest.mark.anyio
async def test_rollout_backfill_releases_marker_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis_mock, publish_mock = _mock_rollout_environment(
        monkeypatch, marker_acquired=True, workspace_ids=[uuid.uuid4()]
    )
    publish_mock.side_effect = ConnectionError("redis unavailable")

    with pytest.raises(ConnectionError):
        await enqueue_rollout_backfill_once()

    redis_mock.delete.assert_awaited_once_with(ROLLOUT_BACKFILL_MARKER_KEY)


def capture_after_commit_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> list[Callable[[], Awaitable[None]]]:
    callbacks: list[Callable[[], Awaitable[None]]] = []

    class _StubQueue:
        def add(self, callback: Callable[[], Awaitable[None]]) -> None:
            callbacks.append(callback)

    monkeypatch.setattr(
        "tracecat.cases.durations.sync_queue.AfterCommitQueue.of",
        classmethod(lambda _cls, _session: _StubQueue()),
    )
    return callbacks


@pytest.mark.anyio
async def test_case_sync_publish_failure_retries_lock_busy_inline_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callbacks = capture_after_commit_callback(monkeypatch)
    publish_mock = AsyncMock(side_effect=ConnectionError("redis unavailable"))
    fallback_mock = AsyncMock(return_value=False)
    monkeypatch.setattr(
        "tracecat.cases.durations.sync_queue.publish_case_duration_sync",
        publish_mock,
    )
    monkeypatch.setattr(
        "tracecat.cases.durations.sync_queue.INLINE_FALLBACK_ATTEMPT_DELAYS_SECONDS",
        (0.0, 0.0),
    )
    workspace_id = uuid.uuid4()
    case_id = uuid.uuid4()

    enqueue_case_duration_sync_after_commit(
        cast(AsyncSession, MagicMock()),
        workspace_id=workspace_id,
        case_id=case_id,
        event_type="case_updated",
        reason="case_event",
        inline_fallback=fallback_mock,
    )

    await callbacks[0]()

    assert fallback_mock.await_count == 2


@pytest.mark.anyio
async def test_case_sync_inline_fallback_stops_after_lock_freed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callbacks = capture_after_commit_callback(monkeypatch)
    publish_mock = AsyncMock(side_effect=ConnectionError("redis unavailable"))
    fallback_mock = AsyncMock(side_effect=[False, True])
    monkeypatch.setattr(
        "tracecat.cases.durations.sync_queue.publish_case_duration_sync",
        publish_mock,
    )
    monkeypatch.setattr(
        "tracecat.cases.durations.sync_queue.INLINE_FALLBACK_ATTEMPT_DELAYS_SECONDS",
        (0.0, 0.0, 0.0),
    )

    enqueue_case_duration_sync_after_commit(
        cast(AsyncSession, MagicMock()),
        workspace_id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        event_type="case_updated",
        reason="case_event",
        inline_fallback=fallback_mock,
    )

    await callbacks[0]()

    assert fallback_mock.await_count == 2


@pytest.mark.anyio
async def test_definition_sync_publish_failure_runs_inline_backfill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callbacks = capture_after_commit_callback(monkeypatch)
    publish_mock = AsyncMock(side_effect=ConnectionError("redis unavailable"))
    inline_backfill_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "tracecat.cases.durations.sync_queue.publish_case_duration_sync",
        publish_mock,
    )

    enqueue_case_duration_sync_after_commit(
        cast(AsyncSession, MagicMock()),
        workspace_id=uuid.uuid4(),
        reason="duration_definition_updated",
        inline_fallback=inline_backfill_mock,
    )

    await callbacks[0]()

    inline_backfill_mock.assert_awaited_once_with()


@pytest.mark.anyio
async def test_failing_inline_fallback_retries_then_logs_without_propagating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callbacks = capture_after_commit_callback(monkeypatch)
    monkeypatch.setattr(
        "tracecat.cases.durations.sync_queue.publish_case_duration_sync",
        AsyncMock(side_effect=ConnectionError("redis unavailable")),
    )
    monkeypatch.setattr(
        "tracecat.cases.durations.sync_queue.INLINE_FALLBACK_ATTEMPT_DELAYS_SECONDS",
        (0.0, 0.0),
    )
    fallback_mock = AsyncMock(side_effect=RuntimeError("database unavailable"))
    logger_mock = MagicMock()
    monkeypatch.setattr(
        "tracecat.cases.durations.sync_queue.logger.exception",
        logger_mock,
    )

    enqueue_case_duration_sync_after_commit(
        cast(AsyncSession, MagicMock()),
        workspace_id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        reason="case_event",
        inline_fallback=fallback_mock,
    )

    await callbacks[0]()

    assert fallback_mock.await_count == 2
    logger_mock.assert_called_once()


@pytest.mark.anyio
async def test_transient_inline_fallback_error_recovers_on_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callbacks = capture_after_commit_callback(monkeypatch)
    monkeypatch.setattr(
        "tracecat.cases.durations.sync_queue.publish_case_duration_sync",
        AsyncMock(side_effect=ConnectionError("redis unavailable")),
    )
    monkeypatch.setattr(
        "tracecat.cases.durations.sync_queue.INLINE_FALLBACK_ATTEMPT_DELAYS_SECONDS",
        (0.0, 0.0, 0.0),
    )
    fallback_mock = AsyncMock(side_effect=[TimeoutError("pool timeout"), True])

    enqueue_case_duration_sync_after_commit(
        cast(AsyncSession, MagicMock()),
        workspace_id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        reason="case_event",
        inline_fallback=fallback_mock,
    )

    await callbacks[0]()

    assert fallback_mock.await_count == 2
