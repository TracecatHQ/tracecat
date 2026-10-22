"""The API sends native commands without writing lifecycle status to SQL."""

import uuid
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import func, select
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from tests.unit.test_durable_workspace_sync import push_inputs
from tracecat.db.models import WorkspaceSyncOperation
from tracecat.exceptions import ScopeDeniedError, TracecatNotFoundError
from tracecat.workspace_sync.operations.domain import fingerprint
from tracecat.workspace_sync.operations.lifecycle_client import SyncLifecycleService
from tracecat.workspace_sync.operations.lifecycle_types import (
    SyncLifecycleInput,
    SyncLifecycleSnapshot,
    SyncLifecycleState,
    SyncPreparedRef,
)
from tracecat.workspace_sync.operations.schemas import (
    SyncOperationCommand,
    SyncOperationCreate,
)
from tracecat.workspace_sync.operations.service import SyncOperationService
from tracecat.workspace_sync.operations.types import (
    SyncOperationConflictError,
    SyncOperationStartError,
    SyncStatus,
)


@pytest.fixture(autouse=True)
def history_session(session, monkeypatch):
    # Unit rows live in the fixture's rollback-only transaction. Only redirect
    # this database boundary; integration tests exercise real service sessions.
    @asynccontextmanager
    async def with_session(role):
        yield SyncOperationService(session, role)

    monkeypatch.setattr(SyncOperationService, "with_session", with_session)


def snapshot(inputs, role, *, status: SyncStatus = "queued", revision=0):
    now = datetime.now(UTC)
    state = SyncLifecycleState(
        status, "fetching", revision, now, now + timedelta(hours=24)
    )
    return SyncLifecycleSnapshot(
        SyncLifecycleInput(inputs, role, fingerprint(inputs.model_dump(mode="json"))),
        state,
    )


def temporal_client(monkeypatch, current):
    handle = Mock(
        query=AsyncMock(side_effect=lambda *a, **kw: current[0]),
        execute_update=AsyncMock(),
    )
    client = Mock(
        start_workflow=AsyncMock(), get_workflow_handle_for=Mock(return_value=handle)
    )
    monkeypatch.setattr(
        "tracecat.workspace_sync.operations.lifecycle_client.get_temporal_client",
        AsyncMock(return_value=client),
    )
    return client, handle


@pytest.mark.anyio
async def test_lost_start_response_has_no_saved_request_and_reuses_identity(
    session, svc_role, monkeypatch
):
    inputs = push_inputs()
    service = SyncLifecycleService(session, svc_role)
    client, _ = temporal_client(monkeypatch, [snapshot(inputs, svc_role)])
    client.start_workflow.side_effect = [
        TimeoutError("Synthetic unsafe provider detail"),
        WorkflowAlreadyStartedError(
            service.workflow_id(inputs.id), "WorkspaceSyncLifecycleWorkflow"
        ),
    ]
    log = Mock()
    monkeypatch.setattr(service, "_log_failure", log)
    with pytest.raises(SyncOperationStartError) as error:
        await service.create(inputs)
    assert error.value.__context__ is None
    assert "unsafe provider" not in str(error.value)
    log.assert_called_once()
    assert (
        await session.scalar(select(func.count()).select_from(WorkspaceSyncOperation))
        == 0
    )
    accepted = await service.create(inputs)
    assert accepted.id == inputs.id and accepted.status == "queued"
    calls = client.start_workflow.await_args_list
    assert [c.kwargs["id"] for c in calls] == [service.workflow_id(inputs.id)] * 2
    assert all(
        c.kwargs["id_reuse_policy"] == WorkflowIDReusePolicy.REJECT_DUPLICATE
        for c in calls
    )
    assert (
        await session.scalar(select(func.count()).select_from(WorkspaceSyncOperation))
        == 0
    )


@pytest.mark.anyio
async def test_reused_operation_id_requires_identical_inputs(
    session, svc_role, monkeypatch
):
    inputs = push_inputs()
    service = SyncLifecycleService(session, svc_role)
    client, _ = temporal_client(monkeypatch, [snapshot(inputs, svc_role)])
    client.start_workflow.side_effect = WorkflowAlreadyStartedError(
        service.workflow_id(inputs.id), "WorkspaceSyncLifecycleWorkflow"
    )
    assert inputs.push is not None
    changed = inputs.model_copy(
        update={"push": inputs.push.model_copy(update={"branch": "sync/changed"})}
    )
    with pytest.raises(SyncOperationConflictError):
        await service.create(changed)


@pytest.mark.anyio
async def test_workflow_query_cannot_expose_another_actor(
    session, svc_role, monkeypatch
):
    inputs = push_inputs()
    owner = svc_role.model_copy(update={"user_id": uuid.uuid4()})
    temporal_client(monkeypatch, [snapshot(inputs, owner)])
    with pytest.raises(TracecatNotFoundError):
        await SyncLifecycleService(session, svc_role).get(inputs.id)


@pytest.mark.anyio
async def test_query_is_authoritative_when_history_is_delayed(
    session, svc_role, monkeypatch
):
    inputs = push_inputs()
    row = WorkspaceSyncOperation(
        id=inputs.id,
        workspace_id=svc_role.workspace_id,
        actor_id=svc_role.actor_id,
        direction="push",
        status="queued",
        stage="fetching",
        attempt=0,
        inputs=inputs.model_dump(mode="json"),
        actor=svc_role.model_dump(mode="json"),
        expires_at=datetime.now(UTC) + timedelta(hours=24),
        summary={"lifecycle_version": 2},
    )
    session.add(row)
    await session.commit()
    value = snapshot(inputs, svc_role, status="applying", revision=7)
    value = replace(
        value,
        state=replace(
            value.state, stage="finishing", data_applied=True, preview_revision=1
        ),
    )
    temporal_client(monkeypatch, [value])
    response = await SyncLifecycleService(session, svc_role).get(inputs.id)
    assert response.status == "applying" and response.stage == "finishing"
    assert response.state_revision == 7 and response.data_applied
    await session.refresh(row)
    assert row.status == "queued" and row.stage == "fetching"
    assert not session.dirty


@pytest.mark.anyio
async def test_lost_update_response_repeats_identical_command_without_sql_transition(
    session, svc_role, monkeypatch
):
    inputs = push_inputs()
    value = snapshot(inputs, svc_role, status="ready", revision=3)
    value = replace(
        value,
        state=replace(
            value.state,
            stage="awaiting_confirmation",
            preview_revision=1,
            preview=SyncPreparedRef(
                "synthetic", "a" * 40, 3, [], value.state.expires_at
            ),
        ),
    )
    current = [value]
    _, handle = temporal_client(monkeypatch, current)
    attempts = 0

    async def update(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError("Synthetic unavailable worker")
        current[0] = replace(
            value,
            state=replace(value.state, status="applying", stage="applying", revision=4),
        )

    handle.execute_update.side_effect = update
    command = SyncOperationCommand(
        command_id=uuid.uuid4(), preview_revision=1, state_revision=3
    )
    service = SyncLifecycleService(session, svc_role)
    with pytest.raises(SyncOperationStartError):
        await service.command(inputs.id, "apply", command)
    assert (await service.command(inputs.id, "apply", command)).status == "applying"
    calls = handle.execute_update.await_args_list
    assert calls[0] == calls[1]
    assert (
        await session.scalar(select(func.count()).select_from(WorkspaceSyncOperation))
        == 0
    )


@pytest.mark.anyio
async def test_start_failure_logs_typed_cause_without_provider_data(
    session, svc_role, monkeypatch
):
    inputs = push_inputs()
    client, _ = temporal_client(monkeypatch, [snapshot(inputs, svc_role)])
    client.start_workflow.side_effect = TimeoutError(
        "Synthetic sensitive provider detail"
    )
    service = SyncLifecycleService(session, svc_role)
    log = Mock()
    monkeypatch.setattr(service.logger, "warning", log)
    with pytest.raises(SyncOperationStartError):
        await service.create(inputs)
    assert log.call_args.kwargs["error_type"] == "TimeoutError"
    assert log.call_args.kwargs["action"] == "start"
    assert "sensitive" not in str(log.call_args)


@pytest.mark.anyio
@pytest.mark.parametrize("status", ["completed", "expired", "failed"])
async def test_terminal_history_survives_temporal_retention(
    session, svc_role, monkeypatch, status
):
    inputs = push_inputs()
    session.add(
        WorkspaceSyncOperation(
            id=inputs.id,
            workspace_id=svc_role.workspace_id,
            actor_id=svc_role.actor_id,
            direction="push",
            status=status,
            stage="finished",
            attempt=0,
            inputs=inputs.model_dump(mode="json"),
            actor=svc_role.model_dump(mode="json"),
            expires_at=datetime.now(UTC),
            summary={
                "lifecycle_version": 2,
                "retryable": False,
                "lifecycle_revision": 8,
            },
        )
    )
    await session.commit()
    client, handle = temporal_client(monkeypatch, [])
    handle.query.side_effect = AssertionError("Retained audit must not query Temporal")
    response = await SyncLifecycleService(session, svc_role).get(inputs.id)
    assert response.status == status and response.state_revision == 8
    client.get_workflow_handle_for.assert_not_called()
    assert (
        await SyncLifecycleService(session, svc_role).create(inputs)
    ).status == status
    client.start_workflow.assert_not_called()
    assert inputs.push is not None
    changed = inputs.model_copy(
        update={"push": inputs.push.model_copy(update={"branch": "sync/changed"})}
    )
    with pytest.raises(SyncOperationConflictError):
        await SyncLifecycleService(session, svc_role).create(changed)


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["apply", "retry"])
@pytest.mark.parametrize("direction", ["push", "pull"])
async def test_push_command_checks_scope_before_consuming_preview(
    session, svc_role, monkeypatch, kind, direction
):
    inputs = (
        push_inputs()
        if direction == "push"
        else SyncOperationCreate.model_validate(
            {
                "id": str(uuid.uuid4()),
                "direction": "pull",
                "pull": {"commit_sha": "a" * 40},
            }
        )
    )
    role = svc_role.model_copy(update={"scopes": frozenset({"workflow:sync"})})
    value = snapshot(
        inputs, role, status="ready" if kind == "apply" else "failed", revision=3
    )
    _, handle = temporal_client(monkeypatch, [value])
    command = SyncOperationCommand(
        command_id=uuid.uuid4(), preview_revision=1, state_revision=3
    )
    if direction == "push":
        with pytest.raises(ScopeDeniedError):
            await SyncLifecycleService(session, role).command(inputs.id, kind, command)
        handle.execute_update.assert_not_awaited()
        # Restoring access can use the same preview and command: none was consumed.
        await SyncLifecycleService(session, svc_role).command(inputs.id, kind, command)
    else:
        await SyncLifecycleService(session, role).command(inputs.id, kind, command)
    handle.execute_update.assert_awaited_once()
