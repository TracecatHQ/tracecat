"""Regression tests for locking, dispatch fairness, and partial sync recovery."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import OperationalError
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from tests.unit.test_durable_workspace_sync import push_inputs
from tracecat.db.models import WorkspaceSyncOperation
from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.schemas import ActionStatement
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.pagination import PageParams
from tracecat.workflow.store.import_service import WorkflowImportService
from tracecat.workflow.store.schemas import RemoteWorkflowDefinition
from tracecat.workspace_sync.operations import activities, dispatch, router
from tracecat.workspace_sync.operations.service import SyncOperationService
from tracecat.workspace_sync.operations.types import SyncOperationRef
from tracecat.workspace_sync.types import SyncCommitConflictError


@pytest.mark.anyio
async def test_waiting_receipt_reads_previous_holders_commit(svc_role):
    async with SyncOperationService.with_session(svc_role) as service:
        operation = await service.create(push_inputs())
    ref = SyncOperationRef(operation.id, svc_role, "apply")

    async def wait_for_receipt():
        async with activities.receipt_context(ref) as (row, _):
            return row.result

    async with activities.receipt_context(ref) as (row, session):
        waiter = asyncio.create_task(wait_for_receipt())
        try:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(waiter), timeout=0.2)
            row.result = {"receipt": "committed"}
            await session.commit()
        except BaseException:
            waiter.cancel()
            raise
    assert await asyncio.wait_for(waiter, timeout=5) == {"receipt": "committed"}


@pytest.mark.anyio
@pytest.mark.parametrize("lookup", ["get_workflow", "resolve_workflow_alias"])
async def test_validation_propagates_database_failure(
    session, svc_role, monkeypatch, lookup
):
    importer = WorkflowImportService(session, svc_role)
    definition = RemoteWorkflowDefinition(
        id=WorkflowUUID.new(uuid.uuid4()).short(),
        definition=DSLInput(
            title="Synthetic workflow",
            description="",
            entrypoint=DSLEntrypoint(ref="child"),
            actions=[
                ActionStatement(
                    ref="child",
                    action="core.workflow.execute",
                    args={"workflow_alias": "synthetic-child"},
                )
            ],
        ),
    )
    monkeypatch.setattr(importer.wf_mgmt, "get_workflow", AsyncMock(return_value=None))
    monkeypatch.setattr(
        importer.wf_mgmt,
        lookup,
        AsyncMock(side_effect=OperationalError("SELECT", {}, ConnectionResetError())),
    )
    with pytest.raises(OperationalError):
        await importer.validate_workflows([definition], normalize_existing=False)


@pytest.mark.anyio
async def test_history_filters_unreadable_operations_before_pagination(
    session, svc_role
):
    service = SyncOperationService(session, svc_role)
    visible = await service.create(push_inputs())
    hidden = await service.create(push_inputs())
    hidden.summary = {"read_scopes": ["variable:read"]}
    await session.commit()
    limited = svc_role.model_copy(
        update={"scopes": frozenset({"workflow:read", "workspace_sync:sync"})}
    )
    page = await SyncOperationService(session, limited).list(PageParams(limit=1))
    assert [item.id for item in page.items] == [visible.id]
    assert page.next_cursor is None


@pytest.mark.anyio
async def test_poison_dispatch_batch_does_not_starve_next_operation(
    svc_role, monkeypatch
):
    now = datetime.now(UTC)
    ids = [uuid.uuid4() for _ in range(101)]
    async with SyncOperationService.with_session(svc_role) as service:
        session = service.session
        for index, operation_id in enumerate(ids):
            inputs = push_inputs().model_copy(update={"id": operation_id})
            session.add(
                WorkspaceSyncOperation(
                    id=operation_id,
                    workspace_id=svc_role.workspace_id,
                    actor_id=svc_role.actor_id,
                    direction="push",
                    status="queued",
                    stage="fetching",
                    inputs=inputs.model_dump(mode="json"),
                    actor={} if index < 100 else svc_role.model_dump(mode="json"),
                    expires_at=now + timedelta(hours=24),
                    next_dispatch_at=now
                    - timedelta(hours=2)
                    + timedelta(seconds=index),
                )
            )
        await session.commit()
    start = AsyncMock()
    monkeypatch.setattr(SyncOperationService, "dispatch", start)
    await dispatch.dispatch_pending_operations()
    start.assert_not_awaited()
    await dispatch.dispatch_pending_operations()
    assert any(call.args[0].id == ids[-1] for call in start.await_args_list)
    async with SyncOperationService.with_session(svc_role) as service:
        first = await service.get(ids[0])
        assert first is not None and first.dispatch_attempts == 1
        assert first.next_dispatch_at > now


@pytest.mark.anyio
@pytest.mark.parametrize("detail", [False, True])
async def test_missing_diff_returns_gone(session, svc_role, monkeypatch, detail):
    service = SyncOperationService(session, svc_role)
    operation = await service.create(push_inputs())
    operation.artifact_key = "synthetic/missing"
    operation.summary = {"diff_count": 1}
    await session.commit()
    monkeypatch.setattr(router, "read_diff", AsyncMock(side_effect=FileNotFoundError))
    monkeypatch.setattr(
        router, "read_diff_page", AsyncMock(side_effect=FileNotFoundError)
    )
    with pytest.raises(HTTPException) as exc:
        if detail:
            await router.get_sync_diff(
                role=svc_role, session=session, operation_id=operation.id, index=0
            )
        else:
            await router.list_sync_diffs(
                role=svc_role, session=session, operation_id=operation.id
            )
    assert exc.value.status_code == 410


@pytest.mark.anyio
async def test_missing_apply_artifact_is_terminal(svc_role, monkeypatch):
    monkeypatch.setattr(
        activities, "refresh_sync_role", AsyncMock(return_value=svc_role)
    )
    monkeypatch.setattr(
        activities, "load_prepared", AsyncMock(side_effect=FileNotFoundError)
    )
    async with SyncOperationService.with_session(svc_role) as service:
        row = await service.create(push_inputs())
        row.status = "applying"
        row.artifact_key = "synthetic/missing"
        await service.session.commit()
    with pytest.raises(ApplicationError) as exc:
        await ActivityEnvironment().run(
            activities.workspace_sync_apply, SyncOperationRef(row.id, svc_role, "apply")
        )
    assert exc.value.non_retryable
    assert exc.value.type == "StaleSyncPreviewError"


@pytest.mark.anyio
async def test_git_conflict_is_classified_as_stale():
    @activities.safe_activity
    async def fail():
        raise SyncCommitConflictError("Synthetic conflict")

    with pytest.raises(ApplicationError) as exc:
        await fail()
    assert exc.value.non_retryable
    assert exc.value.type == "StaleSyncPreviewError"
