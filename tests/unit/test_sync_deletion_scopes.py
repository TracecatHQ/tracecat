"""Deletion roots remain authorized even when their local resource set is empty."""

import uuid
from unittest.mock import AsyncMock

import pytest

from tests.unit.test_durable_workspace_sync import push_inputs
from tracecat.auth.types import Role
from tracecat.exceptions import ScopeDeniedError
from tracecat.git.types import GitUrl
from tracecat.workspace_sync.adapters import WORKSPACE_RESOURCE_ADAPTERS
from tracecat.workspace_sync.operations.domain import (
    DurableSyncService,
    preview_summary,
)
from tracecat.workspace_sync.operations.types import FetchedSync
from tracecat.workspace_sync.schemas import (
    WorkspaceManifest,
    WorkspaceProjection,
    WorkspaceSpec,
)
from tracecat.workspace_sync.transport import VcsTreeSnapshot


@pytest.mark.anyio
@pytest.mark.parametrize("blob_only", [False, True])
async def test_deletion_only_type_requires_read_scope_before_prepare_and_apply(
    monkeypatch, blob_only
):
    scopes = {"workspace_sync:sync"} | {
        adapter.read_scope
        for adapter in WORKSPACE_RESOURCE_ADAPTERS
        if adapter.read_scope
    }
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        scopes=frozenset(scopes - {"variable:read"}),
    )
    sync = DurableSyncService(AsyncMock(), role)
    projection = WorkspaceProjection(
        manifest=WorkspaceManifest(), spec=WorkspaceSpec(), files={}
    )
    monkeypatch.setattr(sync, "project_workspace", AsyncMock(return_value=projection))
    monkeypatch.setattr(sync, "require_entitlement", AsyncMock())
    monkeypatch.setattr(sync, "_adapter_entitled", AsyncMock(return_value=True))
    monkeypatch.setattr(sync, "repository_fingerprint", AsyncMock(return_value="repo"))
    monkeypatch.setattr(
        sync,
        "_workspace_git_url",
        AsyncMock(return_value=GitUrl(host="github.com", org="example", repo="sync")),
    )
    transport = AsyncMock()
    monkeypatch.setattr(sync, "_transport_for_provider", lambda: transport)
    monkeypatch.setattr(
        "tracecat.workspace_sync.operations.domain.try_pg_advisory_xact_lock",
        AsyncMock(return_value=True),
    )
    path = "variables/default/example.yml"
    remote = VcsTreeSnapshot(
        commit_sha="a" * 40,
        tree_sha=None,
        files={}
        if blob_only
        else {path: "name: example\nvalues: {value: synthetic}\n"},
        blob_paths=frozenset({path}),
    )
    fetched = FetchedSync(remote, "repo", "main", True)
    inputs = push_inputs()
    with pytest.raises(ScopeDeniedError) as caught:
        await sync.prepare(inputs, fetched=fetched)
    assert caught.value.missing_scopes == ["variable:read"]
    sync.role = role.model_copy(update={"scopes": frozenset(scopes)})
    prepared = await sync.prepare(inputs, fetched=fetched)
    assert "variable:read" in preview_summary(prepared)["read_scopes"]
    if not blob_only:
        assert prepared.preview.resource_diffs
        assert prepared.preview.resource_diffs[0].change_type == "deleted"
    sync.role = role
    with pytest.raises(ScopeDeniedError) as caught:
        await sync.apply(inputs, prepared, inputs.id)
    assert caught.value.missing_scopes == ["variable:read"]
    transport.write_files.assert_not_awaited()
