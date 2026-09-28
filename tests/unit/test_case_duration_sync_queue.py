import asyncio
import contextlib
import uuid
from collections.abc import Awaitable, Callable
from typing import cast
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.cases.durations.schemas import CaseDurationAnchorSelection
from tracecat.cases.durations.sync_queue import (
    ROLLOUT_BACKFILL_LEASE_SECONDS,
    ROLLOUT_BACKFILL_MARKER_KEY,
    ROLLOUT_BACKFILL_PENDING_SENTINEL,
    enqueue_case_duration_backfill_for_org,
    enqueue_case_duration_backfill_for_orgs,
    enqueue_case_duration_sync_after_commit,
    enqueue_rollout_backfill_once,
    publish_case_duration_sync,
)
from tracecat.cases.enums import CaseEventType
from tracecat.db.models import CaseDurationDefinition, Organization, Workspace


def _duration_definition(
    *, workspace_id: uuid.UUID, name: str
) -> CaseDurationDefinition:
    return CaseDurationDefinition(
        workspace_id=workspace_id,
        name=name,
        start_event_type=CaseEventType.CASE_CREATED,
        start_timestamp_path="created_at",
        start_field_filters={},
        start_selection=CaseDurationAnchorSelection.FIRST,
        end_event_type=CaseEventType.CASE_CLOSED,
        end_timestamp_path="created_at",
        end_field_filters={},
        end_selection=CaseDurationAnchorSelection.FIRST,
    )


@pytest.mark.anyio
async def test_publish_does_not_cap_unconsumed_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis_mock = MagicMock()
    redis_mock.xadd = AsyncMock(return_value="1-0")
    monkeypatch.setattr(
        "tracecat.cases.durations.sync_queue.get_redis_client",
        AsyncMock(return_value=redis_mock),
    )
    workspace_id = uuid.uuid4()
    case_id = uuid.uuid4()

    message_id = await publish_case_duration_sync(
        workspace_id=workspace_id,
        case_id=case_id,
        event_type="case_updated",
        reason="case_event",
    )

    assert message_id == "1-0"
    redis_mock.xadd.assert_awaited_once_with(
        stream_key=config.TRACECAT__CASE_DURATION_SYNC_STREAM_KEY,
        fields={
            "workspace_id": str(workspace_id),
            "reason": "case_event",
            "case_id": str(case_id),
            "event_type": "case_updated",
        },
        expire_seconds=None,
    )


@pytest.mark.anyio
async def test_org_backfill_publishes_only_defined_workspaces_for_org(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization = Organization(
        name=f"Duration Org {uuid.uuid4().hex[:8]}",
        slug=f"duration-org-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    other_organization = Organization(
        name=f"Other Duration Org {uuid.uuid4().hex[:8]}",
        slug=f"other-duration-org-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add_all([organization, other_organization])
    await session.flush()

    workspace_a = Workspace(
        name="Defined workspace A",
        organization_id=organization.id,
    )
    workspace_b = Workspace(
        name="Defined workspace B",
        organization_id=organization.id,
    )
    workspace_without_definitions = Workspace(
        name="Workspace without definitions",
        organization_id=organization.id,
    )
    other_workspace = Workspace(
        name="Other org defined workspace",
        organization_id=other_organization.id,
    )
    session.add_all(
        [
            workspace_a,
            workspace_b,
            workspace_without_definitions,
            other_workspace,
        ]
    )
    await session.flush()
    session.add_all(
        [
            _duration_definition(workspace_id=workspace_a.id, name="First A"),
            _duration_definition(workspace_id=workspace_a.id, name="Second A"),
            _duration_definition(workspace_id=workspace_b.id, name="First B"),
            _duration_definition(workspace_id=other_workspace.id, name="Other"),
        ]
    )
    await session.commit()

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

    await enqueue_case_duration_backfill_for_org(organization.id)

    publish_mock.assert_has_awaits(
        [
            call(workspace_id=workspace_a.id, reason="duration_definition_updated"),
            call(workspace_id=workspace_b.id, reason="duration_definition_updated"),
        ],
        any_order=True,
    )
    assert publish_mock.await_count == 2


@pytest.mark.anyio
async def test_orgs_backfill_publishes_defined_workspaces_for_all_selected_orgs(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_a = Organization(
        name=f"Duration Org A {uuid.uuid4().hex[:8]}",
        slug=f"duration-org-a-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    organization_b = Organization(
        name=f"Duration Org B {uuid.uuid4().hex[:8]}",
        slug=f"duration-org-b-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    unrelated_organization = Organization(
        name=f"Unrelated Duration Org {uuid.uuid4().hex[:8]}",
        slug=f"unrelated-duration-org-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add_all([organization_a, organization_b, unrelated_organization])
    await session.flush()

    workspace_a = Workspace(
        name="Selected org A workspace",
        organization_id=organization_a.id,
    )
    workspace_b = Workspace(
        name="Selected org B workspace",
        organization_id=organization_b.id,
    )
    unrelated_workspace = Workspace(
        name="Unrelated org workspace",
        organization_id=unrelated_organization.id,
    )
    session.add_all([workspace_a, workspace_b, unrelated_workspace])
    await session.flush()
    session.add_all(
        [
            _duration_definition(workspace_id=workspace_a.id, name="Org A"),
            _duration_definition(workspace_id=workspace_b.id, name="Org B"),
            _duration_definition(
                workspace_id=unrelated_workspace.id,
                name="Unrelated",
            ),
        ]
    )
    await session.commit()

    session_context_call_count = 0

    @contextlib.asynccontextmanager
    async def fake_session_context():
        nonlocal session_context_call_count
        session_context_call_count += 1
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

    await enqueue_case_duration_backfill_for_orgs(
        [organization_a.id, organization_b.id]
    )

    publish_mock.assert_has_awaits(
        [
            call(workspace_id=workspace_a.id, reason="duration_definition_updated"),
            call(workspace_id=workspace_b.id, reason="duration_definition_updated"),
        ],
        any_order=True,
    )
    assert publish_mock.await_count == 2
    assert session_context_call_count == 1


def _mock_org_backfill_environment(
    monkeypatch: pytest.MonkeyPatch,
    *,
    organization_id: uuid.UUID,
    workspace_ids: list[uuid.UUID],
    case_ids: list[uuid.UUID],
) -> AsyncMock:
    session = MagicMock()
    workspace_result = MagicMock()
    workspace_result.tuples.return_value.all.return_value = [
        (organization_id, workspace_id) for workspace_id in workspace_ids
    ]
    case_result = MagicMock()
    case_result.scalars.return_value.all.return_value = case_ids
    session.execute = AsyncMock(side_effect=[workspace_result, case_result])

    @contextlib.asynccontextmanager
    async def fake_session_context():
        yield session

    monkeypatch.setattr(
        "tracecat.cases.durations.sync_queue.get_async_session_bypass_rls_context_manager",
        fake_session_context,
    )
    publish_mock = AsyncMock(side_effect=[ConnectionError("redis unavailable"), "1-0"])
    monkeypatch.setattr(
        "tracecat.cases.durations.sync_queue.publish_case_duration_sync",
        publish_mock,
    )
    return publish_mock


@pytest.mark.anyio
async def test_org_backfill_publish_failure_syncs_inline_and_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id = uuid.uuid4()
    failed_workspace_id = uuid.uuid4()
    published_workspace_id = uuid.uuid4()
    case_ids = [uuid.uuid4(), uuid.uuid4()]
    publish_mock = _mock_org_backfill_environment(
        monkeypatch,
        organization_id=organization_id,
        workspace_ids=[failed_workspace_id, published_workspace_id],
        case_ids=case_ids,
    )
    sync_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "tracecat.cases.durations.materialization.sync_case_duration",
        sync_mock,
    )

    await enqueue_case_duration_backfill_for_org(organization_id)

    assert publish_mock.await_args_list == [
        call(
            workspace_id=failed_workspace_id,
            reason="duration_definition_updated",
        ),
        call(
            workspace_id=published_workspace_id,
            reason="duration_definition_updated",
        ),
    ]
    assert sync_mock.await_args_list == [
        call(failed_workspace_id, case_id, event_types=None) for case_id in case_ids
    ]


@pytest.mark.anyio
async def test_org_backfill_inline_retry_syncs_lock_contended_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id = uuid.uuid4()
    failed_workspace_id = uuid.uuid4()
    case_id = uuid.uuid4()
    _mock_org_backfill_environment(
        monkeypatch,
        organization_id=organization_id,
        workspace_ids=[failed_workspace_id],
        case_ids=[case_id],
    )
    sync_mock = AsyncMock(side_effect=[False, True])
    sleep_mock = AsyncMock()
    monkeypatch.setattr(
        "tracecat.cases.durations.materialization.sync_case_duration",
        sync_mock,
    )
    monkeypatch.setattr("tracecat.cases.durations.sync_queue.asyncio.sleep", sleep_mock)

    await enqueue_case_duration_backfill_for_org(organization_id)

    assert sync_mock.await_args_list == [
        call(failed_workspace_id, case_id, event_types=None),
        call(failed_workspace_id, case_id, event_types=None),
    ]
    sleep_mock.assert_any_await(0.5)


@pytest.mark.anyio
async def test_org_backfill_inline_sync_failure_isolated_and_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id = uuid.uuid4()
    failed_workspace_id = uuid.uuid4()
    failed_case_id = uuid.uuid4()
    successful_case_id = uuid.uuid4()
    _mock_org_backfill_environment(
        monkeypatch,
        organization_id=organization_id,
        workspace_ids=[failed_workspace_id],
        case_ids=[failed_case_id, successful_case_id],
    )
    sync_mock = AsyncMock(
        side_effect=[RuntimeError("database unavailable"), True, True]
    )
    sleep_mock = AsyncMock()
    warning_mock = MagicMock()
    monkeypatch.setattr(
        "tracecat.cases.durations.materialization.sync_case_duration",
        sync_mock,
    )
    monkeypatch.setattr("tracecat.cases.durations.sync_queue.asyncio.sleep", sleep_mock)
    monkeypatch.setattr(
        "tracecat.cases.durations.sync_queue.logger.warning", warning_mock
    )

    await enqueue_case_duration_backfill_for_org(organization_id)

    assert sync_mock.await_args_list == [
        call(failed_workspace_id, failed_case_id, event_types=None),
        call(failed_workspace_id, successful_case_id, event_types=None),
        call(failed_workspace_id, failed_case_id, event_types=None),
    ]
    warning_mock.assert_any_call(
        "Inline organization duration backfill case sync failed",
        organization_id=str(organization_id),
        workspace_id=str(failed_workspace_id),
        case_id=str(failed_case_id),
        error="database unavailable",
    )


@pytest.mark.anyio
async def test_org_backfill_inline_lock_stays_busy_logs_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id = uuid.uuid4()
    failed_workspace_id = uuid.uuid4()
    case_id = uuid.uuid4()
    _mock_org_backfill_environment(
        monkeypatch,
        organization_id=organization_id,
        workspace_ids=[failed_workspace_id],
        case_ids=[case_id],
    )
    sync_mock = AsyncMock(return_value=False)
    sleep_mock = AsyncMock()
    warning_mock = MagicMock()
    monkeypatch.setattr(
        "tracecat.cases.durations.materialization.sync_case_duration",
        sync_mock,
    )
    monkeypatch.setattr("tracecat.cases.durations.sync_queue.asyncio.sleep", sleep_mock)
    monkeypatch.setattr(
        "tracecat.cases.durations.sync_queue.logger.warning", warning_mock
    )

    await enqueue_case_duration_backfill_for_org(organization_id)

    assert sync_mock.await_count == 4
    warning_mock.assert_any_call(
        "Inline organization duration backfill skipped cases; lock busy or sync failed",
        organization_id=str(organization_id),
        workspace_id=str(failed_workspace_id),
        remaining_case_count=1,
        attempts=4,
    )


@pytest.mark.anyio
async def test_org_backfill_inline_failure_does_not_block_other_workspaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id = uuid.uuid4()
    failed_workspace_id = uuid.uuid4()
    published_workspace_id = uuid.uuid4()
    publish_mock = _mock_org_backfill_environment(
        monkeypatch,
        organization_id=organization_id,
        workspace_ids=[failed_workspace_id, published_workspace_id],
        case_ids=[uuid.uuid4()],
    )
    sync_mock = AsyncMock(side_effect=RuntimeError("database unavailable"))
    sleep_mock = AsyncMock()
    warning_mock = MagicMock()
    exception_mock = MagicMock()
    monkeypatch.setattr(
        "tracecat.cases.durations.materialization.sync_case_duration",
        sync_mock,
    )
    monkeypatch.setattr("tracecat.cases.durations.sync_queue.asyncio.sleep", sleep_mock)
    monkeypatch.setattr(
        "tracecat.cases.durations.sync_queue.logger.warning", warning_mock
    )
    monkeypatch.setattr(
        "tracecat.cases.durations.sync_queue.logger.exception", exception_mock
    )

    await enqueue_case_duration_backfill_for_org(organization_id)

    assert publish_mock.await_args_list[-1] == call(
        workspace_id=published_workspace_id,
        reason="duration_definition_updated",
    )
    assert sync_mock.await_count == 4
    warning_mock.assert_any_call(
        "Inline organization duration backfill skipped cases; lock busy or sync failed",
        organization_id=str(organization_id),
        workspace_id=str(failed_workspace_id),
        remaining_case_count=1,
        attempts=4,
    )
    exception_mock.assert_not_called()


def _mock_rollout_environment(
    monkeypatch: pytest.MonkeyPatch,
    *,
    marker_acquired: bool,
    marker_value: str | None = None,
    workspace_ids: list[uuid.UUID],
) -> tuple[MagicMock, AsyncMock]:
    redis_mock = MagicMock()
    redis_mock.set_if_not_exists = AsyncMock(return_value=marker_acquired)
    redis_mock.get = AsyncMock(return_value=marker_value)
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

    assert await enqueue_rollout_backfill_once()

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
async def test_rollout_backfill_returns_false_while_competing_lease_is_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis_mock, publish_mock = _mock_rollout_environment(
        monkeypatch,
        marker_acquired=False,
        marker_value=ROLLOUT_BACKFILL_PENDING_SENTINEL,
        workspace_ids=[uuid.uuid4()],
    )

    assert not await enqueue_rollout_backfill_once()

    redis_mock.get.assert_awaited_once_with(ROLLOUT_BACKFILL_MARKER_KEY)
    publish_mock.assert_not_awaited()


@pytest.mark.anyio
async def test_rollout_backfill_returns_true_when_permanent_marker_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis_mock, publish_mock = _mock_rollout_environment(
        monkeypatch,
        marker_acquired=False,
        marker_value="2026-07-22T12:00:00+00:00",
        workspace_ids=[uuid.uuid4()],
    )

    assert await enqueue_rollout_backfill_once()

    redis_mock.get.assert_awaited_once_with(ROLLOUT_BACKFILL_MARKER_KEY)
    publish_mock.assert_not_awaited()


@pytest.mark.anyio
async def test_rollout_backfill_returns_false_when_competing_lease_lapses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis_mock, publish_mock = _mock_rollout_environment(
        monkeypatch,
        marker_acquired=False,
        marker_value=None,
        workspace_ids=[uuid.uuid4()],
    )

    assert not await enqueue_rollout_backfill_once()

    redis_mock.get.assert_awaited_once_with(ROLLOUT_BACKFILL_MARKER_KEY)
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
