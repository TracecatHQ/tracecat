"""Authorization ceilings and attempt fences survive retries and reconciliation."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.unit.test_durable_workspace_sync import push_inputs
from tracecat.exceptions import TracecatAuthorizationError
from tracecat.workspace_sync.operations import activities, auth
from tracecat.workspace_sync.operations.service import SyncOperationService
from tracecat.workspace_sync.operations.types import SyncOperationRef


@pytest.mark.anyio
async def test_confirmation_and_retry_preserve_original_scope_ceiling(
    session, svc_role
):
    original = svc_role.model_copy(
        update={"scopes": frozenset({"workspace:workflow:read"})}
    )
    service = SyncOperationService(session, original)
    operation = await service.create(push_inputs())
    captured = dict(operation.actor)
    operation.status = "ready"
    await session.commit()
    elevated = SyncOperationService(session, svc_role)
    await elevated.apply(operation.id)
    assert operation.actor == captured
    operation.status = "failed"
    await session.commit()
    await elevated.retry(operation.id)
    assert operation.actor == captured


@pytest.mark.anyio
async def test_stale_attempt_cannot_replace_current_stage(svc_role):
    async with SyncOperationService.with_session(svc_role) as service:
        operation = await service.create(push_inputs())
        operation.attempt = 1
        operation.stage = "preparing"
        await service.session.commit()
        ref = SyncOperationRef(operation.id, svc_role, "preview", attempt=0)
    await activities.set_stage(ref, "fetching")
    async with SyncOperationService.with_session(svc_role) as service:
        current = await service.get(ref.operation_id)
        assert current.status == "queued"
        assert current.stage == "preparing"


@pytest.mark.anyio
async def test_inactive_organization_revokes_queued_user(svc_role, monkeypatch):
    role = svc_role.model_copy(update={"type": "user", "is_platform_superuser": False})
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(is_active=True, is_superuser=False)
    session.scalar.side_effect = [role.user_id, False]
    monkeypatch.setattr(
        auth,
        "query_effective_scopes",
        AsyncMock(return_value=frozenset({"org:workspace:read"})),
    )
    with pytest.raises(TracecatAuthorizationError, match="Organization access"):
        await auth.refresh_sync_role(session, role)


@pytest.mark.anyio
async def test_service_account_requires_current_entitlement(svc_role, monkeypatch):
    role = svc_role.model_copy(
        update={
            "type": "service_account",
            "user_id": None,
            "service_account_id": uuid.uuid4(),
        }
    )
    entitled = AsyncMock(return_value=False)
    monkeypatch.setattr(auth, "is_org_entitled", entitled)
    session = AsyncMock()
    with pytest.raises(TracecatAuthorizationError, match="Service account access"):
        await auth.refresh_sync_role(session, role)
    entitled.assert_awaited_once()
    session.scalar.assert_not_awaited()


@pytest.mark.anyio
async def test_failed_pull_preview_does_not_claim_ready_to_review(
    svc_role, monkeypatch
):
    from temporalio.testing import ActivityEnvironment

    from tests.unit.test_durable_workspace_sync import prepared_diff
    from tracecat.sync import PullResult
    from tracecat.workflow.store.schemas import WorkflowSyncPullRequest
    from tracecat.workspace_sync.operations.domain import DurableSyncService
    from tracecat.workspace_sync.operations.schemas import SyncOperationCreate

    prepared = prepared_diff(0)
    prepared.preview = PullResult(
        success=False,
        diagnostics=[],
        message="Invalid input",
        commit_sha="a" * 40,
        workflows_found=0,
        workflows_imported=0,
    )
    monkeypatch.setattr(
        activities, "refresh_sync_role", AsyncMock(return_value=svc_role)
    )
    monkeypatch.setattr(DurableSyncService, "fetch_remote", AsyncMock())
    monkeypatch.setattr(DurableSyncService, "prepare", AsyncMock(return_value=prepared))
    monkeypatch.setattr(
        DurableSyncService,
        "repository_fingerprint",
        AsyncMock(return_value="repository"),
    )
    monkeypatch.setattr(
        DurableSyncService, "local_fingerprint", AsyncMock(return_value="local")
    )
    monkeypatch.setattr(
        activities, "store_prepared", AsyncMock(return_value="synthetic-preview")
    )
    async with SyncOperationService.with_session(svc_role) as service:
        operation = await service.create(
            SyncOperationCreate(
                id=uuid.uuid4(),
                direction="pull",
                pull=WorkflowSyncPullRequest(commit_sha="a" * 40),
            )
        )
        ref = SyncOperationRef(operation.id, svc_role, "preview")
    await ActivityEnvironment().run(activities.workspace_sync_prepare, ref)
    async with SyncOperationService.with_session(svc_role) as service:
        operation = await service.get(ref.operation_id)
        assert operation.status == "failed"
        assert operation.stage == "preparing"
        assert not service.read(operation).can_retry
