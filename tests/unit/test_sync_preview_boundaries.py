"""Changes hidden by published definitions still invalidate a reviewed pull."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from tests.support.fake_vcs import FakeVcsServer
from tracecat.db.models import (
    Action,
    AgentPreset,
    CaseFields,
    MCPIntegration,
    Skill,
    Workflow,
    Workspace,
    WorkspaceSyncResourceMapping,
    WorkspaceVariable,
)
from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.enums import PlatformAction
from tracecat.dsl.schemas import ActionStatement
from tracecat.git.types import GitUrl
from tracecat.integrations.enums import MCPAuthType
from tracecat.sync import PullResult
from tracecat.workflow.store.schemas import WorkflowSyncPullRequest
from tracecat.workspace_sync.adapters.registry import CASE_FIELD_RESOURCE_ADAPTER
from tracecat.workspace_sync.enums import VcsProvider
from tracecat.workspace_sync.operations.domain import DurableSyncService
from tracecat.workspace_sync.operations.mappings import surviving_mapping_targets
from tracecat.workspace_sync.operations.schemas import (
    PreparedSync,
    SyncOperationCreate,
    SyncPushResult,
)
from tracecat.workspace_sync.operations.types import FetchedSync, StaleSyncPreviewError
from tracecat.workspace_sync.schemas import (
    AgentPresetResourceSpec,
    WorkflowResourceSpec,
    WorkspaceManifest,
    WorkspaceProjection,
    WorkspaceRemoteSnapshot,
    WorkspaceSpec,
    WorkspaceSyncExportRequest,
)
from tracecat.workspace_sync.service import WorkspaceSyncService
from tracecat.workspace_sync.transport import VcsTreeSnapshot
from tracecat.workspace_sync.types import PreparedPullPreview, SyncMappingTarget


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
    assert all(
        diff.diff == "Non-text file will be deleted; textual diff unavailable."
        for diff in changes
    )


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


@pytest.mark.anyio
async def test_confirmation_holds_repository_settings_lock_through_import(
    svc_role, monkeypatch
):
    async with DurableSyncService.with_session(svc_role) as sync:
        workspace = await sync._workspace()
        workspace.settings = {
            "git_repo_url": "git+ssh://git@github.com/example/demo.git",
            "git_provider": VcsProvider.GITHUB,
        }
        await sync.session.commit()
        inputs = SyncOperationCreate(
            id=uuid.uuid4(),
            direction="pull",
            pull=WorkflowSyncPullRequest(commit_sha="a" * 40),
        )
        preview = PullResult(
            success=True,
            commit_sha="a" * 40,
            workflows_found=0,
            workflows_imported=0,
            diagnostics=[],
            message="Synthetic preview",
        )
        prepared = PreparedSync(
            snapshot=WorkspaceRemoteSnapshot(
                commit_sha="a" * 40, files={}, spec=WorkspaceSpec()
            ),
            preview=preview,
            workspace_fingerprint="local",
            repository_fingerprint=await sync.repository_fingerprint(),
            compare_ref="a" * 40,
            compare_sha="a" * 40,
        )
        await sync.session.rollback()
        monkeypatch.setattr(sync, "require_entitlement", AsyncMock())
        monkeypatch.setattr(sync, "_require_spec_entitlements", AsyncMock())
        monkeypatch.setattr(sync, "local_fingerprint", AsyncMock(return_value="local"))
        monkeypatch.setattr(
            sync,
            "project_workspace",
            AsyncMock(
                return_value=WorkspaceProjection(
                    manifest=WorkspaceManifest(), spec=WorkspaceSpec(), files={}
                )
            ),
        )
        lock_row = (
            select(Workspace)
            .where(Workspace.id == svc_role.workspace_id)
            .with_for_update(nowait=True)
        )

        async def import_snapshot(*args, **kwargs):
            # A concurrent settings writer must wait until confirmation commits.
            async with DurableSyncService.with_session(svc_role) as writer:
                with pytest.raises(DBAPIError) as error:
                    await writer.session.execute(lock_row)
                assert getattr(error.value.orig, "sqlstate", None) == "55P03"
            # Unrelated child rows need only a foreign-key key-share lock.
            async with DurableSyncService.with_session(svc_role) as writer:
                await writer.session.execute(text("SET LOCAL lock_timeout = '200ms'"))
                writer.session.add(
                    WorkspaceVariable(
                        workspace_id=svc_role.workspace_id,
                        name="synthetic-concurrent-child",
                        environment="default",
                        values={"value": "example"},
                    )
                )
                await writer.session.flush()
            return preview

        monkeypatch.setattr(sync, "import_prepared_snapshot", import_snapshot)
        result = await sync.apply(inputs, prepared, inputs.id)
        assert isinstance(result, PullResult) and result.success
        await sync.session.commit()
        # The fence is transaction-scoped; settings become editable afterward.
        async with DurableSyncService.with_session(svc_role) as writer:
            assert await writer.session.scalar(lock_row) is not None


@pytest.mark.anyio
async def test_replaced_unmapped_variable_rejects_push_before_git_write(
    session, svc_role, monkeypatch
):
    variable = WorkspaceVariable(
        workspace_id=svc_role.workspace_id,
        name="synthetic-variable",
        environment="default",
        values={"value": "example"},
    )
    session.add(variable)
    await session.commit()
    sync = DurableSyncService(session, svc_role)
    inputs = SyncOperationCreate(
        id=uuid.uuid4(),
        direction="push",
        push=WorkspaceSyncExportRequest(
            branch="sync/demo", message="Synthetic sync", create_pr=False
        ),
    )
    remote = VcsTreeSnapshot(commit_sha="a" * 40, tree_sha=None, files={})
    transport = AsyncMock()
    transport.branch_exists.return_value = True
    transport.read_files.return_value = remote
    monkeypatch.setattr(sync, "_transport_for_provider", lambda: transport)
    monkeypatch.setattr(sync, "require_entitlement", AsyncMock())
    monkeypatch.setattr(sync, "_require_spec_entitlements", AsyncMock())
    monkeypatch.setattr(sync, "repository_fingerprint", AsyncMock(return_value="repo"))
    monkeypatch.setattr(
        sync,
        "_workspace_git_url",
        AsyncMock(return_value=GitUrl(host="github.com", org="example", repo="demo")),
    )
    prepared = await sync.prepare(
        inputs, fetched=FetchedSync(remote, "repo", "main", True)
    )
    assert prepared.projection is not None
    original_id = variable.id
    await session.delete(variable)
    await session.flush()
    replacement = WorkspaceVariable(
        workspace_id=svc_role.workspace_id,
        name="synthetic-variable",
        environment="default",
        values={"value": "example"},
    )
    session.add(replacement)
    await session.commit()
    current = await sync.project_export_preview(
        resource_ids=None, include_schedules=False
    )
    assert current.files == prepared.projection.files
    assert replacement.id != original_id
    with pytest.raises(StaleSyncPreviewError):
        await sync.apply(inputs, prepared, inputs.id)
    transport.write_files.assert_not_awaited()
    assert not list(await session.scalars(select(WorkspaceSyncResourceMapping)))


@pytest.mark.anyio
async def test_push_mapping_fingerprint_ignores_iteration_order(svc_role):
    sync = DurableSyncService(AsyncMock(), svc_role)
    inputs = SyncOperationCreate(
        id=uuid.uuid4(),
        direction="push",
        push=WorkspaceSyncExportRequest(
            branch="sync/demo", message="Synthetic sync", create_pr=False
        ),
    )
    targets = [
        SyncMappingTarget("variable", name, f"variables/{name}.yaml", uuid.uuid4())
        for name in ["alpha", "beta"]
    ]
    projection = WorkspaceProjection(
        manifest=WorkspaceManifest(),
        spec=WorkspaceSpec(),
        files={},
        mapping_targets=targets,
    )
    reviewed = await sync.local_fingerprint(inputs, projection=projection)
    reordered = projection.model_copy(
        update={"mapping_targets": list(reversed(targets))}
    )
    assert await sync.local_fingerprint(inputs, projection=reordered) == reviewed


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["delete", "replace", "rename"])
async def test_push_recovery_maps_only_surviving_reviewed_resources(
    session, svc_role, monkeypatch, change
):
    server = FakeVcsServer()
    url = GitUrl(host="github.com", org="example", repo="synthetic-sync")
    workspace = await session.scalar(
        select(Workspace).where(Workspace.id == svc_role.workspace_id)
    )
    assert workspace is not None
    workspace.settings = {"git_repo_url": url.to_url(), "git_provider": "github"}
    variable = WorkspaceVariable(
        workspace_id=svc_role.workspace_id,
        name="synthetic-variable",
        environment="default",
        values={"value": "example"},
    )
    session.add(variable)
    await session.commit()
    original_id = variable.id
    sync = DurableSyncService(
        session, svc_role, transport_factory=server.transport_factory
    )
    monkeypatch.setattr(sync, "require_entitlement", AsyncMock())
    monkeypatch.setattr(sync, "_require_spec_entitlements", AsyncMock())
    inputs = SyncOperationCreate(
        id=uuid.uuid4(),
        direction="push",
        push=WorkspaceSyncExportRequest(
            branch="sync/demo", message="Synthetic sync", create_pr=False
        ),
    )
    prepared = await sync.prepare(inputs)
    transport = sync._transport_for_provider()
    write = transport.write_files
    writes = 0

    async def lose_first_acknowledgement(**kwargs):
        nonlocal writes
        writes += 1
        commit = await write(**kwargs)
        if writes == 1:
            raise ConnectionResetError("Synthetic lost acknowledgement")
        return commit

    monkeypatch.setattr(transport, "write_files", lose_first_acknowledgement)
    monkeypatch.setattr(sync, "_transport_for_provider", lambda: transport)
    with pytest.raises(ConnectionResetError):
        await sync.apply(inputs, prepared, inputs.id)
    await session.rollback()
    history = await transport.list_commits(url=url, branch="sync/demo")
    variable = await session.scalar(
        select(WorkspaceVariable).where(WorkspaceVariable.id == original_id)
    )
    assert variable is not None
    if change == "rename":
        variable.name = "renamed-after-push"
    else:
        await session.delete(variable)
        await session.flush()
        if change == "replace":
            session.add(
                WorkspaceVariable(
                    workspace_id=svc_role.workspace_id,
                    name="synthetic-variable",
                    environment="default",
                    values={"value": "example"},
                )
            )
    await session.commit()
    result = await sync.apply(inputs, prepared, inputs.id)
    assert isinstance(result, SyncPushResult)
    assert result.commit.sha == history[0].sha
    assert await transport.list_commits(url=url, branch="sync/demo") == history
    assert (
        writes == 2
    )  # Recovery finishes transport bookkeeping without another commit.
    mappings = list(await session.scalars(select(WorkspaceSyncResourceMapping)))
    if change == "rename":
        assert len(mappings) == 1
        assert mappings[0].local_id == original_id
        assert mappings[0].source_path == "variables/default/synthetic-variable.yml"
    else:
        assert mappings == []  # Never rebind reviewed contents to the replacement UUID.


@pytest.mark.anyio
@pytest.mark.parametrize("skill_removal", ["deleted", "archived"])
async def test_recovery_omits_soft_deleted_resources_and_removed_schema_fields(
    session, svc_role, skill_removal
):
    preset = AgentPreset(
        workspace_id=svc_role.workspace_id,
        name="Synthetic preset",
        slug="synthetic",
        model_name="synthetic",
        model_provider="synthetic",
    )
    skill = Skill(
        workspace_id=svc_role.workspace_id, name="Synthetic skill", slug="synthetic"
    )
    fields = CaseFields(workspace_id=svc_role.workspace_id, schema={"synthetic": {}})
    session.add_all([preset, skill, fields])
    await session.commit()
    sync = DurableSyncService(session, svc_role)
    projected = await CASE_FIELD_RESOURCE_ADAPTER.project(sync)
    assert len(projected.resources) == 1
    field = projected.resources[0]
    targets = [
        SyncMappingTarget(
            "agent_preset", "synthetic", "agent-presets/synthetic.yml", preset.id
        ),
        SyncMappingTarget("skill", "synthetic", "skills/synthetic/skill.yml", skill.id),
        SyncMappingTarget(
            "case_field", field.source_id, field.source_path, field.local_id
        ),
    ]
    assert await surviving_mapping_targets(sync, targets) == targets
    preset.deleted_at = datetime.now(UTC)
    if skill_removal == "deleted":
        skill.deleted_at = datetime.now(UTC)
    else:
        skill.archived_at = datetime.now(UTC)
    fields.schema = {}
    await session.commit()
    assert await surviving_mapping_targets(sync, targets) == []
