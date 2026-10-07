"""Regression coverage for preview boundaries and post-commit recovery."""

import hashlib
import uuid
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from tests.support.fake_vcs import FakeVcsServer
from tests.unit.test_durable_workspace_sync import prepared_diff, push_inputs
from tracecat.db.models import (
    Action,
    Workflow,
    Workspace,
    WorkspaceSyncResourceMapping,
    WorkspaceVariable,
)
from tracecat.exceptions import EntitlementRequired
from tracecat.git.types import GitUrl
from tracecat.sync import PullResult, PushStatus
from tracecat.tiers.enums import Entitlement
from tracecat.workflow.store.schemas import WorkflowSyncPullRequest
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.importer import WorkspaceResourceImportService
from tracecat.workspace_sync.operations import (
    storage,
)
from tracecat.workspace_sync.operations.domain import DurableSyncService
from tracecat.workspace_sync.operations.schemas import PreparedSync, SyncOperationCreate
from tracecat.workspace_sync.operations.types import (
    StaleSyncPreviewError,
)
from tracecat.workspace_sync.schemas import (
    SkillFileSpec,
    SkillResourceSpec,
    WorkspaceManifest,
    WorkspaceProjection,
    WorkspaceRemoteSnapshot,
    WorkspaceSpec,
    WorkspaceSyncExportResult,
)


@pytest.mark.anyio
@pytest.mark.parametrize("fail_after_mapping", [False, True])
async def test_unchanged_pull_adopts_variable_identity(
    session, svc_role, monkeypatch, fail_after_mapping
):
    variable = WorkspaceVariable(
        workspace_id=svc_role.workspace_id,
        name="example",
        environment="default",
        values={"value": "local-only"},
    )
    unrelated = WorkspaceVariable(
        workspace_id=svc_role.workspace_id,
        name="unrelated",
        environment="default",
        values={"value": "unrelated"},
    )
    session.add_all([variable, unrelated])
    await session.commit()
    variable_id = variable.id
    sync = DurableSyncService(session, svc_role)
    projection = await sync.project_workspace(
        resource_ids={SyncResourceType.VARIABLE: {variable_id}},
        create_missing_mappings=False,
    )
    snapshot = WorkspaceRemoteSnapshot(
        commit_sha="a" * 40, files=projection.files, spec=projection.spec
    )
    source_id = next(iter(snapshot.spec.variables))
    mapping_query = select(WorkspaceSyncResourceMapping).where(
        WorkspaceSyncResourceMapping.workspace_id == svc_role.workspace_id
    )
    assert list(await session.scalars(mapping_query)) == []
    diffs = await sync._resource_diffs_for_pull(snapshot, sync_schedules=False)
    assert diffs == []

    if fail_after_mapping:
        upsert = sync._upsert_mappings

        async def fail_after_upsert(targets):
            await upsert(targets)
            raise RuntimeError("Synthetic receipt failure")

        with monkeypatch.context() as patcher:
            patcher.setattr(sync, "_upsert_mappings", fail_after_upsert)
            with pytest.raises(RuntimeError, match="Synthetic receipt failure"):
                await sync.import_prepared_snapshot(
                    snapshot, sync_schedules=False, changed_resources=set()
                )
        assert list(await session.scalars(mapping_query)) == []

    inputs = SyncOperationCreate(
        id=uuid.uuid4(),
        direction="pull",
        pull=WorkflowSyncPullRequest(commit_sha=snapshot.commit_sha),
    )
    prepared = PreparedSync(
        snapshot=snapshot,
        preview=PullResult(
            success=True,
            commit_sha=snapshot.commit_sha,
            workflows_found=0,
            workflows_imported=0,
            diagnostics=[],
            message="No changes",
            resource_diffs=diffs,
        ),
        workspace_fingerprint=await sync.local_fingerprint(inputs),
        repository_fingerprint="repo",
        compare_ref=snapshot.commit_sha,
        compare_sha=snapshot.commit_sha,
    )
    monkeypatch.setattr(sync, "require_entitlement", AsyncMock())
    monkeypatch.setattr(sync, "repository_fingerprint", AsyncMock(return_value="repo"))
    monkeypatch.setattr(
        sync,
        "_workspace_git_url",
        AsyncMock(return_value=GitUrl(host="github.com", org="example", repo="test")),
    )
    project = AsyncMock(wraps=sync.project_workspace)
    monkeypatch.setattr(sync, "project_workspace", project)
    importer = AsyncMock(side_effect=AssertionError("Unchanged resource re-imported"))
    with monkeypatch.context() as patcher:
        patcher.setattr(
            WorkspaceResourceImportService, "import_non_workflow_resources", importer
        )
        result = await sync.apply(inputs, prepared, inputs.id)
        await session.commit()
    # The fingerprint projection also supplies identities; import must not
    # project the entire type again (or download skill blobs a second time).
    project.assert_awaited_once()
    assert isinstance(result, PullResult)
    assert result.success
    assert result.resource_counts is not None
    assert result.resource_counts["variable"].imported == 0
    mappings = list(await session.scalars(mapping_query))
    assert [(mapping.source_id, mapping.local_id) for mapping in mappings] == [
        (source_id, variable_id)
    ]
    await session.refresh(variable)
    variable.name = "renamed"
    await session.commit()

    diffs = await sync._resource_diffs_for_pull(snapshot, sync_schedules=False)
    result = await sync.import_prepared_snapshot(
        snapshot,
        sync_schedules=False,
        changed_resources={(diff.resource_type, diff.source_id) for diff in diffs},
        commit=True,
    )
    assert result.success
    assert result.resource_counts is not None
    assert result.resource_counts["variable"].imported == 1
    await session.refresh(variable)
    assert variable.id == variable_id
    assert variable.name == "example"
    assert variable.values == {"value": "local-only"}
    variables = list(
        await session.scalars(
            select(WorkspaceVariable).where(
                WorkspaceVariable.workspace_id == svc_role.workspace_id
            )
        )
    )
    assert {row.name for row in variables} == {"example", "unrelated"}
    assert len(variables) == 2


@pytest.mark.anyio
async def test_skill_contents_survive_private_artifact_roundtrip(
    session, svc_role, monkeypatch
):
    objects = {}

    async def upload(content, key):
        objects[key] = content

    async def download(key):
        return objects[key]

    monkeypatch.setattr(storage, "_upload", upload)
    monkeypatch.setattr(storage, "_download", download)
    content = "---\nname: example\ndescription: Synthetic instructions\n---\n# Example skill\n"
    skill = SkillResourceSpec(
        id="example",
        slug="example",
        name="example",
        description="Synthetic instructions",
        files=[
            SkillFileSpec(
                path="SKILL.md", sha256=hashlib.sha256(content.encode()).hexdigest()
            )
        ],
        file_contents={"SKILL.md": content},
    )
    prepared = prepared_diff(0)
    prepared.snapshot = WorkspaceRemoteSnapshot(
        commit_sha="a" * 40, files={}, spec=WorkspaceSpec(skills={"example": skill})
    )
    key = await storage.store_prepared("synthetic", prepared)
    restored = await storage.load_prepared(key)
    assert restored.snapshot is not None
    assert restored.snapshot.spec.skills["example"].file_contents == {
        "SKILL.md": content
    }
    assert (
        "file_contents" not in skill.model_dump()
    )  # Git manifest remains metadata-only.
    result = await DurableSyncService(session, svc_role).import_prepared_snapshot(
        restored.snapshot,
        sync_schedules=False,
    )
    assert result.success, result.diagnostics
    assert result.resource_counts is not None
    assert result.resource_counts["skill"].imported == 1


@pytest.mark.anyio
async def test_pull_guard_detects_draft_action_edits_even_with_same_export(
    session, svc_role, monkeypatch
):
    workflow = Workflow(
        workspace_id=svc_role.workspace_id, title="Example", description="Synthetic"
    )
    session.add(workflow)
    await session.flush()
    action = Action(
        workspace_id=svc_role.workspace_id,
        workflow_id=workflow.id,
        title="Example",
        description="",
        type="core.transform.reshape",
        inputs="value: before",
    )
    session.add(action)
    await session.commit()
    sync = DurableSyncService(session, svc_role)
    # A published export intentionally stays constant while its draft changes.

    projection = WorkspaceProjection(
        manifest=WorkspaceManifest(), spec=WorkspaceSpec(), files={}
    )
    monkeypatch.setattr(sync, "project_workspace", AsyncMock(return_value=projection))
    inputs = SyncOperationCreate(
        id=uuid.uuid4(),
        direction="pull",
        pull=WorkflowSyncPullRequest(commit_sha="a" * 40),
    )
    before = await sync.local_fingerprint(inputs)
    workflow.viewport_x = 100
    workflow.viewport_y = 50
    workflow.viewport_zoom = 2
    workflow.trigger_position_x = 40
    workflow.graph_version += 1
    await session.commit()
    assert await sync.local_fingerprint(inputs) == before
    action.inputs = "value: after"
    await session.commit()
    assert await sync.local_fingerprint(inputs) != before


@pytest.mark.anyio
async def test_immutable_pull_rechecks_git_sync_entitlement(
    session, svc_role, monkeypatch
):
    sync = DurableSyncService(session, svc_role)
    check = AsyncMock(side_effect=EntitlementRequired(Entitlement.GIT_SYNC))
    monkeypatch.setattr(sync, "require_entitlement", check)
    importer = AsyncMock()
    monkeypatch.setattr(sync, "import_prepared_snapshot", importer)
    inputs = SyncOperationCreate(
        id=uuid.uuid4(),
        direction="pull",
        pull=WorkflowSyncPullRequest(commit_sha="a" * 40),
    )
    with pytest.raises(EntitlementRequired):
        await sync.apply(inputs, prepared_diff(0), inputs.id)
    check.assert_awaited_once_with(Entitlement.GIT_SYNC)
    importer.assert_not_awaited()


@pytest.mark.anyio
async def test_new_push_with_matching_contents_remains_no_op(session, svc_role):
    server = FakeVcsServer()
    url = GitUrl(host="github.com", org="example", repo="sync-test")
    workspace = await session.scalar(
        select(Workspace).where(Workspace.id == svc_role.workspace_id)
    )
    assert workspace is not None
    workspace.settings = {"git_repo_url": url.to_url(), "git_provider": "github"}
    await session.commit()
    sync = DurableSyncService(
        session, svc_role, transport_factory=server.transport_factory
    )
    first_inputs = push_inputs()
    first_prepared = await sync.prepare(first_inputs)
    first = await sync.apply(first_inputs, first_prepared, first_inputs.id)
    assert isinstance(first, WorkspaceSyncExportResult)
    assert first.commit.status == PushStatus.COMMITTED

    inputs = push_inputs()
    prepared = await sync.prepare(inputs)
    result = await sync.apply(inputs, prepared, inputs.id)
    assert isinstance(result, WorkspaceSyncExportResult)
    assert result.commit.status == PushStatus.NO_OP
    assert result.commit.sha is None


@pytest.mark.anyio
@pytest.mark.parametrize("create_pr", [True, False])
@pytest.mark.parametrize(
    "recovery_case",
    [
        "head",
        "ancestor",
        "missing_marker",
        "managed_change",
        "stale_file",
        "history_race",
    ],
)
async def test_push_recovers_committed_tree_after_workspace_rename(
    session, svc_role, monkeypatch, create_pr, recovery_case
):
    server = FakeVcsServer()
    url = GitUrl(host="github.com", org="example", repo="sync-test")
    workspace = await session.scalar(
        select(Workspace).where(Workspace.id == svc_role.workspace_id)
    )
    assert workspace is not None
    workspace.settings = {"git_repo_url": url.to_url(), "git_provider": "github"}
    variable = WorkspaceVariable(
        workspace_id=svc_role.workspace_id,
        name="example",
        environment="default",
        values={"value": "reviewed"},
    )
    session.add(variable)
    await session.commit()
    variable_id = variable.id
    sync = DurableSyncService(
        session, svc_role, transport_factory=server.transport_factory
    )
    inputs = push_inputs()
    assert inputs.push is not None
    inputs.push.create_pr = create_pr
    prepared = await sync.prepare(inputs)
    assert prepared.projection is not None
    transport = sync._transport_for_provider()
    write = transport.write_files
    writes = 0

    async def interrupted_write(**kwargs):
        nonlocal writes
        writes += 1
        if recovery_case == "missing_marker":
            kwargs["operation_id"] = uuid.uuid4()
        commit = await write(**kwargs)
        if writes == 1:
            # The Git write succeeded, but the requested PR and DB receipt did not.
            raise ConnectionResetError("Lost Git acknowledgement")
        assert commit.status == PushStatus.NO_OP
        return replace(
            commit, pr_url="https://example.test/pull/1" if create_pr else None
        )

    monkeypatch.setattr(transport, "write_files", interrupted_write)
    monkeypatch.setattr(sync, "_transport_for_provider", lambda: transport)
    with pytest.raises(ConnectionResetError):
        await sync.apply(inputs, prepared, inputs.id)
    await session.rollback()
    first = await transport.list_commits(url=url, branch="sync/test")
    external_files = {
        "ancestor": {"README.md": "Unrelated update"},
        "managed_change": {"variables/default/example.yml": "Changed managed content"},
        "stale_file": {"variables/default/extra.yml": "Unreviewed managed file"},
    }
    if recovery_case in external_files:
        await write(
            url=url,
            files=external_files[recovery_case],
            message="External update",
            branch="sync/test",
            create_pr=False,
        )
    history = await transport.list_commits(url=url, branch="sync/test")
    if recovery_case == "history_race":
        list_commits = transport.list_commits

        async def moving_history(**kwargs):
            await write(
                url=url,
                files={"README.md": "Update after snapshot read"},
                message="Concurrent update",
                branch="sync/test",
                create_pr=False,
            )
            return await list_commits(**kwargs)

        monkeypatch.setattr(transport, "list_commits", moving_history)
    variable = await session.scalar(
        select(WorkspaceVariable).where(WorkspaceVariable.id == variable_id)
    )
    assert variable is not None
    variable.name = "renamed-after-push"
    variable.values = {"value": "unreviewed"}
    await session.commit()
    if recovery_case not in {"head", "ancestor"}:
        with pytest.raises(StaleSyncPreviewError):
            await sync.apply(inputs, prepared, inputs.id)
        assert writes == 1
        return
    result = await sync.apply(inputs, prepared, inputs.id)
    assert isinstance(result, WorkspaceSyncExportResult)
    assert result.commit.status == PushStatus.COMMITTED
    assert result.commit.sha == first[0].sha
    assert result.commit.message == inputs.push.message
    assert result.commit.pr_url == (
        "https://example.test/pull/1" if create_pr else None
    )
    assert await transport.list_commits(url=url, branch="sync/test") == history
    assert writes == 2
    assert "unreviewed" not in "".join(server.repo_files(url, ref="sync/test").values())
    mapping = await session.scalar(
        select(WorkspaceSyncResourceMapping).where(
            WorkspaceSyncResourceMapping.local_id == variable_id
        )
    )
    assert mapping is not None
    assert mapping.source_path == "variables/default/example.yml"
    assert variable.name == "renamed-after-push"


@pytest.mark.anyio
@pytest.mark.parametrize("already_matching", [False, True])
@pytest.mark.parametrize("existing_before_preview", [False, True])
async def test_push_rejects_new_target_at_unchanged_base(
    session, svc_role, monkeypatch, already_matching, existing_before_preview
):
    server = FakeVcsServer()
    url = GitUrl(host="github.com", org="example", repo="sync-test")
    workspace = await session.scalar(
        select(Workspace).where(Workspace.id == svc_role.workspace_id)
    )
    assert workspace is not None
    workspace.settings = {"git_repo_url": url.to_url(), "git_provider": "github"}
    session.add(
        WorkspaceVariable(
            workspace_id=svc_role.workspace_id,
            name="example",
            environment="default",
            values={"value": "reviewed"},
        )
    )
    await session.commit()
    sync = DurableSyncService(
        session, svc_role, transport_factory=server.transport_factory
    )
    inputs = push_inputs()
    transport = sync._transport_for_provider()
    if already_matching:
        projection = await sync.project_workspace()
        base_files = projection.files
    else:
        base_files = {"README.md": "Unrelated base content"}
    await transport.write_files(
        url=url,
        files=base_files,
        message="Base content",
        branch="main",
        create_pr=False,
    )
    # Simulate a target omitted by capped/paginated branch enumeration.
    monkeypatch.setattr(server._repo(url), "branch_names", lambda **kwargs: ["main"])
    if existing_before_preview:
        await transport.write_files(
            url=url,
            files=base_files,
            message="Existing target",
            branch="sync/test",
            create_pr=False,
        )
    prepared = await sync.prepare(inputs)
    assert prepared.target_exists == existing_before_preview
    # An unchanged write creates the fake branch pointing at the existing base SHA.
    await transport.write_files(
        url=url,
        files=base_files,
        message="External branch creation",
        branch="sync/test",
        create_pr=False,
    )
    current = await transport.read_files(url=url, ref="sync/test")
    assert current.commit_sha == prepared.compare_sha
    writer = AsyncMock(wraps=transport.write_files)
    monkeypatch.setattr(transport, "write_files", writer)
    monkeypatch.setattr(sync, "_transport_for_provider", lambda: transport)
    if existing_before_preview:
        await sync.apply(inputs, prepared, inputs.id)
        writer.assert_awaited_once()
        return
    with pytest.raises(StaleSyncPreviewError):
        await sync.apply(inputs, prepared, inputs.id)
    writer.assert_not_awaited()
    assert (
        await transport.read_files(url=url, ref="sync/test")
    ).commit_sha == current.commit_sha
