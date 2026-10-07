"""Keep remote preview work outside the fenced database snapshot."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from tests.support.fake_vcs import FakeVcsServer, FakeVcsTransport
from tests.unit.test_durable_workspace_sync import push_inputs
from tracecat.db.models import Workspace, WorkspaceVariable
from tracecat.git.types import GitUrl
from tracecat.workspace_sync.enums import VcsProvider
from tracecat.workspace_sync.operations import activities, storage
from tracecat.workspace_sync.operations.domain import DurableSyncService
from tracecat.workspace_sync.operations.service import SyncOperationService
from tracecat.workspace_sync.operations.types import SyncOperationRef


@pytest.mark.anyio
@pytest.mark.parametrize("edit_during_upload", [False, True])
async def test_prepare_fences_local_state_without_holding_snapshot_over_network(
    svc_role, monkeypatch, edit_during_upload
):
    server = FakeVcsServer()
    url = GitUrl(host="github.com", org="example", repo="sync-test")
    async with SyncOperationService.with_session(svc_role) as service:
        workspace = await service.session.scalar(
            select(Workspace).where(Workspace.id == svc_role.workspace_id)
        )
        assert workspace is not None
        workspace.settings = {
            "git_repo_url": url.to_url(),
            "git_provider": VcsProvider.GITHUB,
        }
        variable = WorkspaceVariable(
            workspace_id=svc_role.workspace_id,
            name="example",
            environment="default",
            values={"value": "original"},
        )
        service.session.add(variable)
        await service.session.commit()
        variable_id = variable.id
        operation = await service.create(push_inputs())
        operation_id = operation.id

    depth = 0
    remote_reads = 0
    local_preparations = 0
    uploads = 0
    original_context = activities.operation_context
    original_read = FakeVcsTransport.read_files
    original_prepare = DurableSyncService.prepare

    @asynccontextmanager
    async def tracked_context(ref):
        nonlocal depth
        async with original_context(ref) as context:
            depth += 1
            try:
                yield context
            finally:
                depth -= 1

    async def remote_read(self, **kwargs):
        nonlocal remote_reads
        assert depth == 0, "Git reads must run outside the serializable context"
        remote_reads += 1
        return await original_read(self, **kwargs)

    async def local_prepare(self, inputs, **kwargs):
        nonlocal local_preparations
        assert depth == 1, "Local projection must retain one fenced snapshot"
        local_preparations += 1
        return await original_prepare(self, inputs, **kwargs)

    async def upload(_content, _key):
        nonlocal uploads
        assert depth == 0, "Artifact uploads must run outside the snapshot"
        uploads += 1
        if edit_during_upload and uploads == 1:
            async with SyncOperationService.with_session(svc_role) as writer:
                changed = await writer.session.scalar(
                    select(WorkspaceVariable).where(WorkspaceVariable.id == variable_id)
                )
                assert changed is not None
                changed.description = "Edited during artifact upload"
                await writer.session.commit()

    monkeypatch.setattr(activities, "operation_context", tracked_context)
    monkeypatch.setattr(
        activities, "refresh_sync_role", AsyncMock(return_value=svc_role)
    )
    monkeypatch.setattr(FakeVcsTransport, "read_files", remote_read)
    monkeypatch.setattr(DurableSyncService, "prepare", local_prepare)
    monkeypatch.setattr(storage, "_upload", upload)
    monkeypatch.setattr(
        "tracecat.workspace_sync.service.vcs_transport_for_provider",
        server.transport_factory,
    )
    ref = SyncOperationRef(operation_id, svc_role, "preview")
    environment = ActivityEnvironment()
    if edit_during_upload:
        with pytest.raises(ApplicationError) as failure:
            await environment.run(activities.workspace_sync_prepare, ref)
        assert failure.value.type == "StaleSyncPreviewError"
    else:
        await environment.run(activities.workspace_sync_prepare, ref)

    assert remote_reads == 1
    assert local_preparations == 1
    assert uploads > 0
    assert depth == 0
    async with SyncOperationService.with_session(svc_role) as service:
        persisted = await service.get(operation_id)
        if edit_during_upload:
            assert persisted.status != "ready"
            assert persisted.artifact_key is None
        else:
            assert persisted.status == "ready"
            assert persisted.artifact_key is not None
