import uuid
from collections.abc import Awaitable, Callable
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.cases.durations.sync_queue import (
    enqueue_case_duration_sync_after_commit,
)


def capture_after_commit_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> list[Callable[[], Awaitable[None]]]:
    callbacks: list[Callable[[], Awaitable[None]]] = []

    def capture_callback(
        session: AsyncSession, callback: Callable[[], Awaitable[None]]
    ) -> None:
        del session
        callbacks.append(callback)

    monkeypatch.setattr(
        "tracecat.cases.durations.sync_queue.add_after_commit_callback",
        capture_callback,
    )
    return callbacks


@pytest.mark.anyio
async def test_case_sync_publish_failure_runs_inline_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callbacks = capture_after_commit_callback(monkeypatch)
    publish_mock = AsyncMock(side_effect=ConnectionError("redis unavailable"))
    fallback_mock = AsyncMock(return_value=False)
    monkeypatch.setattr(
        "tracecat.cases.durations.sync_queue.publish_case_duration_sync",
        publish_mock,
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

    fallback_mock.assert_awaited_once_with()


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
async def test_failing_inline_fallback_is_logged_and_does_not_propagate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callbacks = capture_after_commit_callback(monkeypatch)
    monkeypatch.setattr(
        "tracecat.cases.durations.sync_queue.publish_case_duration_sync",
        AsyncMock(side_effect=ConnectionError("redis unavailable")),
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

    fallback_mock.assert_awaited_once_with()
    logger_mock.assert_called_once()
