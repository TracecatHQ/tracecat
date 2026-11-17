"""Case-duration effects survive durable sync receipt retries."""

import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from tests.unit.test_durable_workspace_sync import prepared_diff
from tests.unit.test_workspace_sync_case_duration_backfill import (
    _duration_spec,
    _snapshot,
)
from tracecat.cases.durations.reconciliation import CaseDurationBackfills
from tracecat.cases.enums import CaseEventType
from tracecat.db.models import CaseDurationDefinition
from tracecat.db.session_events import AfterCommitQueue
from tracecat.workflow.store.schemas import WorkflowSyncPullRequest
from tracecat.workspace_sync.operations import activities, reconciliation
from tracecat.workspace_sync.operations.domain import DurableSyncService
from tracecat.workspace_sync.operations.schemas import SyncOperationCreate
from tracecat.workspace_sync.operations.service import SyncOperationService
from tracecat.workspace_sync.operations.types import SyncOperationRef
from tracecat.workspace_sync.service import WorkspaceSyncService


@pytest.mark.anyio
async def test_duration_capture_tracks_material_changes_and_rolls_back(
    session, svc_role
):
    service = WorkspaceSyncService(session, svc_role)
    spec = _duration_spec("synthetic-duration")
    with CaseDurationBackfills.capture(session) as created:
        result = await service._import_snapshot(
            _snapshot(spec, commit_sha="a" * 40), sync_schedules=False, commit=False
        )
        assert result.success
        assert created.workspaces == [svc_role.workspace_id]
        assert not AfterCommitQueue.of(session).callbacks
    assert CaseDurationBackfills.of(session) is None

    with CaseDurationBackfills.capture(session) as metadata:
        result = await service._import_snapshot(
            _snapshot(
                spec.model_copy(update={"description": "Metadata only"}),
                commit_sha="b" * 40,
            ),
            sync_schedules=False,
            commit=False,
        )
        assert result.success
        assert metadata.workspaces == []

    with CaseDurationBackfills.capture(session) as changed:
        result = await service._import_snapshot(
            _snapshot(
                _duration_spec(
                    "synthetic-duration", end_event=CaseEventType.STATUS_CHANGED
                ),
                commit_sha="c" * 40,
            ),
            sync_schedules=False,
            commit=False,
        )
        assert result.success
        assert changed.workspaces == [svc_role.workspace_id]
        assert not AfterCommitQueue.of(session).callbacks
    await session.rollback()
    assert (
        await session.scalar(
            select(CaseDurationDefinition.id).where(
                CaseDurationDefinition.workspace_id == svc_role.workspace_id
            )
        )
        is None
    )


@pytest.mark.anyio
@pytest.mark.parametrize("lost_publish_ack", [False, True])
async def test_duration_receipt_retries_without_reimporting(
    svc_role, monkeypatch, lost_publish_ack
):
    monkeypatch.setattr(
        activities, "refresh_sync_role", AsyncMock(return_value=svc_role)
    )
    monkeypatch.setattr(
        activities, "load_prepared", AsyncMock(return_value=prepared_diff(0))
    )
    volatile_publish = AsyncMock()
    monkeypatch.setattr(
        "tracecat.cases.durations.sync_queue.publish_case_duration_sync",
        volatile_publish,
    )
    imports = 0

    async def import_duration(service, inputs, prepared, operation_id):
        nonlocal imports
        imports += 1
        return await service._import_snapshot(
            _snapshot(_duration_spec("synthetic-duration"), commit_sha="a" * 40),
            sync_schedules=False,
            commit=False,
        )

    monkeypatch.setattr(DurableSyncService, "apply", import_duration)
    publications = []
    attempts = 0

    async def publish(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            if lost_publish_ack:
                publications.append(kwargs)
            raise ConnectionResetError("Synthetic queue interruption")
        publications.append(kwargs)
        return "1-0"

    monkeypatch.setattr(reconciliation, "publish_case_duration_sync", publish)
    async with SyncOperationService.with_session(svc_role) as service:
        operation = await service.create(
            SyncOperationCreate(
                id=uuid.uuid4(),
                direction="pull",
                pull=WorkflowSyncPullRequest(commit_sha="a" * 40),
            )
        )
        operation.status = "applying"
        operation.artifact_key = "prepared-duration"
        await service.session.commit()
        ref = SyncOperationRef(operation.id, svc_role, "apply")

    env = ActivityEnvironment()
    with pytest.raises(ApplicationError):
        await env.run(activities.workspace_sync_apply, ref)
    async with SyncOperationService.with_session(svc_role) as service:
        operation = await service.get(ref.operation_id)
        assert operation.status == "applying"
        assert operation.result is not None
        assert operation.summary is not None
        assert operation.summary["case_duration_backfills"] == {
            "workspaces": [str(svc_role.workspace_id)]
        }
        assert (
            await service.session.scalar(
                select(CaseDurationDefinition.id).where(
                    CaseDurationDefinition.workspace_id == svc_role.workspace_id
                )
            )
            is not None
        )

    # A fresh activity context uses the committed outbox even after access changes.
    refresh = AsyncMock(
        side_effect=AssertionError("Must not authorize a second import")
    )
    monkeypatch.setattr(activities, "refresh_sync_role", refresh)
    await env.run(activities.workspace_sync_apply, ref)
    await env.run(activities.workspace_sync_apply, ref)
    async with SyncOperationService.with_session(svc_role) as service:
        assert (await service.get(ref.operation_id)).status == "completed"
    assert imports == 1
    assert attempts == 2
    assert len(publications) == (2 if lost_publish_ack else 1)
    assert all(
        item
        == {
            "workspace_id": svc_role.workspace_id,
            "reason": "duration_definition_updated",
        }
        for item in publications
    )
    volatile_publish.assert_not_awaited()
    refresh.assert_not_awaited()
