"""Changes hidden by published definitions still invalidate a reviewed pull."""

import uuid
from unittest.mock import AsyncMock

import pytest

from tracecat.db.models import Action, MCPIntegration, Workflow
from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.enums import PlatformAction
from tracecat.dsl.schemas import ActionStatement
from tracecat.git.types import GitUrl
from tracecat.integrations.enums import MCPAuthType
from tracecat.sync import PullResult
from tracecat.workflow.store.schemas import WorkflowSyncPullRequest
from tracecat.workspace_sync.operations.domain import DurableSyncService
from tracecat.workspace_sync.operations.schemas import PreparedSync, SyncOperationCreate
from tracecat.workspace_sync.operations.types import FetchedSync, StaleSyncPreviewError
from tracecat.workspace_sync.schemas import (
    AgentPresetResourceSpec,
    WorkflowResourceSpec,
    WorkspaceManifest,
    WorkspaceProjection,
    WorkspaceRemoteSnapshot,
    WorkspaceSpec,
)
from tracecat.workspace_sync.service import WorkspaceSyncService
from tracecat.workspace_sync.transport import VcsTreeSnapshot
from tracecat.workspace_sync.types import PreparedPullPreview


@pytest.mark.anyio
@pytest.mark.parametrize("changed", ["order", "lock"])
async def test_draft_changes_hidden_by_published_files_reject_confirmation(
    session, svc_role, monkeypatch, changed
):
    workflow = Workflow(
        workspace_id=svc_role.workspace_id,
        title="Synthetic workflow",
        description="Synthetic",
        expects={"first": {}, "second": {}},
        expects_order=["first", "second"],
    )
    session.add(workflow)
    await session.flush()
    action = Action(
        workspace_id=svc_role.workspace_id,
        workflow_id=workflow.id,
        type="core.transform.reshape",
        title="Synthetic action",
        description="Synthetic",
        inputs="value: example",
    )
    session.add(action)
    await session.commit()
    projection = WorkspaceProjection(
        manifest=WorkspaceManifest(), spec=WorkspaceSpec(), files={}
    )
    sync = DurableSyncService(session, svc_role)
    inputs = SyncOperationCreate(
        id=uuid.uuid4(),
        direction="pull",
        pull=WorkflowSyncPullRequest(commit_sha="a" * 40),
    )
    monkeypatch.setattr(sync, "project_workspace", AsyncMock(return_value=projection))
    monkeypatch.setattr(sync, "require_entitlement", AsyncMock())
    monkeypatch.setattr(sync, "repository_fingerprint", AsyncMock(return_value="repo"))
    monkeypatch.setattr(
        sync,
        "_workspace_git_url",
        AsyncMock(return_value=GitUrl(host="github.com", org="example", repo="demo")),
    )
    reviewed = await sync.local_fingerprint(inputs, projection=projection)
    prepared = PreparedSync(
        snapshot=WorkspaceRemoteSnapshot(
            commit_sha="a" * 40, files={}, spec=WorkspaceSpec()
        ),
        preview=PullResult(
            success=True,
            commit_sha="a" * 40,
            workflows_found=0,
            workflows_imported=0,
            diagnostics=[],
            message="Synthetic preview",
        ),
        workspace_fingerprint=reviewed,
        repository_fingerprint="repo",
        compare_ref="a" * 40,
        compare_sha="a" * 40,
    )
    if changed == "order":
        workflow.expects_order = ["second", "first"]
    else:
        action.locked_execution_id = "synthetic-execution"
    await session.commit()
    with pytest.raises(StaleSyncPreviewError):
        await sync.apply(inputs, prepared, inputs.id)
    assert workflow.expects_order == (
        ["second", "first"] if changed == "order" else ["first", "second"]
    )
    assert action.locked_execution_id == (
        "synthetic-execution" if changed == "lock" else None
    )


@pytest.mark.anyio
async def test_push_preview_lists_every_deleted_blob_path(svc_role):
    projection = WorkspaceProjection(
        manifest=WorkspaceManifest(), spec=WorkspaceSpec(), files={}
    )
    # Text decoding omits these blobs; Git deletion still sees all tree paths.
    remote = VcsTreeSnapshot(
        commit_sha="a" * 40,
        tree_sha=None,
        files={},
        blob_paths=frozenset(
            {"skills/old/asset.bin", "workflows/orphan.bin", "README.bin"}
        ),
    )
    sync = WorkspaceSyncService(session=AsyncMock(), role=svc_role)
    changes = sync._resource_diffs_for_export(
        projection, remote, delete_missing_paths_under=["skills", "workflows"]
    )
    assert {diff.source_path for diff in changes} == {
        "skills/old/asset.bin",
        "workflows/orphan.bin",
    }
    assert all(diff.change_type == "deleted" for diff in changes)
    assert all("text" in diff.diff.lower() for diff in changes)


@pytest.mark.anyio
@pytest.mark.parametrize("reference", ["preset", "workflow"])
@pytest.mark.parametrize("change", ["delete", "server", "unrelated"])
async def test_selected_mcp_target_is_revalidated_before_import(
    session, svc_role, monkeypatch, reference, change
):
    target = MCPIntegration(
        workspace_id=svc_role.workspace_id,
        name="Synthetic target",
        slug="target",
        server_type="http",
        server_uri="https://synthetic.invalid/mcp",
        auth_type=MCPAuthType.NONE,
    )
    unrelated = MCPIntegration(
        workspace_id=svc_role.workspace_id,
        name="Unrelated target",
        slug="unrelated",
        server_type="http",
        server_uri="https://unrelated.invalid/mcp",
        auth_type=MCPAuthType.NONE,
    )
    session.add_all([target, unrelated])
    await session.commit()
    spec = WorkspaceSpec()
    if reference == "preset":
        spec.agent_presets["demo"] = AgentPresetResourceSpec(
            id="demo",
            slug="demo",
            name="Synthetic preset",
            mcp_integrations=[str(target.id)],
        )
    else:
        spec.workflows["demo"] = WorkflowResourceSpec(
            id="demo",
            definition=DSLInput(
                title="Synthetic workflow",
                description="Synthetic integration reference",
                entrypoint=DSLEntrypoint(ref="agent", expects={}),
                actions=[
                    ActionStatement(
                        ref="agent",
                        action=PlatformAction.AI_AGENT.value,
                        args={"mcp_integrations": [str(target.id)]},
                    )
                ],
            ),
        )
    snapshot = WorkspaceRemoteSnapshot(commit_sha="a" * 40, files={}, spec=spec)
    preview = PullResult(
        success=True,
        commit_sha="a" * 40,
        workflows_found=0,
        workflows_imported=0,
        diagnostics=[],
        message="Synthetic preview",
    )
    sync = DurableSyncService(session, svc_role)
    inputs = SyncOperationCreate(
        id=uuid.uuid4(),
        direction="pull",
        pull=WorkflowSyncPullRequest(commit_sha="a" * 40),
    )
    monkeypatch.setattr(sync, "require_entitlement", AsyncMock())
    monkeypatch.setattr(sync, "_require_spec_entitlements", AsyncMock())
    monkeypatch.setattr(sync, "repository_fingerprint", AsyncMock(return_value="repo"))
    monkeypatch.setattr(
        sync,
        "_workspace_git_url",
        AsyncMock(return_value=GitUrl(host="github.com", org="example", repo="demo")),
    )
    monkeypatch.setattr(sync, "parse_files", AsyncMock(return_value=(snapshot, [])))
    # Correlation has already resolved the selected local target in this snapshot.
    monkeypatch.setattr(
        sync,
        "prepare_pull_preview",
        AsyncMock(return_value=PreparedPullPreview(snapshot, preview)),
    )
    monkeypatch.setattr(
        sync,
        "project_workspace",
        AsyncMock(
            return_value=WorkspaceProjection(
                manifest=WorkspaceManifest(), spec=WorkspaceSpec(), files={}
            )
        ),
    )
    importer = AsyncMock(return_value=preview)
    monkeypatch.setattr(sync, "import_prepared_snapshot", importer)
    prepared = await sync.prepare(
        inputs,
        fetched=FetchedSync(
            VcsTreeSnapshot(commit_sha="a" * 40, tree_sha=None, files={}),
            "repo",
            "a" * 40,
            True,
        ),
    )
    if change == "delete":
        await session.delete(target)
    elif change == "server":
        target.server_uri = "https://changed.invalid/mcp"
    else:
        unrelated.server_uri = "https://changed-unrelated.invalid/mcp"
    await session.commit()
    if change == "unrelated":
        result = await sync.apply(inputs, prepared, inputs.id)
        assert isinstance(result, PullResult) and result.success
        importer.assert_awaited_once()
    else:
        with pytest.raises(StaleSyncPreviewError):
            await sync.apply(inputs, prepared, inputs.id)
        importer.assert_not_awaited()
