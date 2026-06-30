"""Tests for simple workspace sync service behavior."""

from __future__ import annotations

import base64
import uuid
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, patch

import pytest
from github.GithubException import GithubException

from tests.support.fake_vcs import FakeVcsServer
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.cases.enums import CaseEventType
from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.schemas import ActionStatement
from tracecat.exceptions import (
    EntitlementRequired,
    ScopeDeniedError,
    TracecatValidationError,
)
from tracecat.git.types import GitUrl
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.sync import CommitInfo, PullOptions, PushStatus
from tracecat.workflow.store.schemas import RemoteCaseTrigger, RemoteWorkflowSchedule
from tracecat.workspace_sync.adapters import (
    SECRET_METADATA_RESOURCE_ADAPTER,
    VARIABLE_RESOURCE_ADAPTER,
)
from tracecat.workspace_sync.enums import SyncResourceType, VcsProvider
from tracecat.workspace_sync.schemas import (
    MANIFEST_FILENAME,
    AgentPresetResourceSpec,
    AgentPresetSkillBinding,
    AgentPresetVersionResourceSpec,
    CaseDropdownResourceSpec,
    ResourceRef,
    SecretMetadataResourceSpec,
    SkillResourceSpec,
    VariableResourceSpec,
    WorkflowResourceSpec,
    WorkspaceManifest,
    WorkspaceManifestResources,
    WorkspaceProjection,
    WorkspaceSpec,
    WorkspaceSyncExportPreviewRequest,
    WorkspaceSyncExportRequest,
)
from tracecat.workspace_sync.serialization import canonical_json_text
from tracecat.workspace_sync.service import WorkspaceSyncService, _table_names
from tracecat.workspace_sync.transport import (
    GitHubWorkspaceSyncTransport,
    VcsTreeSnapshot,
    unsupported_transport,
)
from tracecat.workspace_sync.workflow import (
    serialize_workflow_spec,
    workflow_source_path,
    workflow_spec_to_remote,
    workflow_spec_with_source_workflow_ids,
)


@pytest.fixture
def sample_dsl() -> DSLInput:
    return DSLInput(
        title="Sync me",
        description="A workflow for workspace sync tests",
        entrypoint=DSLEntrypoint(ref="start", expects={}),
        actions=[
            ActionStatement(
                ref="start",
                action="core.transform.passthrough",
                args={"value": "test"},
            )
        ],
    )


@pytest.fixture
def workspace_sync_service() -> WorkspaceSyncService:
    session = AsyncMock()
    transaction = AsyncMock()
    transaction.__aenter__.return_value = None
    transaction.__aexit__.return_value = None
    session.begin_nested = Mock(return_value=transaction)
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-api"],
    )
    return WorkspaceSyncService(session=session, role=role)


@pytest.mark.anyio
async def test_pull_does_not_sync_schedules_by_default(
    workspace_sync_service: WorkspaceSyncService,
    sample_dsl: DSLInput,
) -> None:
    spec = WorkflowResourceSpec(
        id="sync-me",
        alias="sync-me",
        definition=sample_dsl,
    )
    files = {
        "tracecat.json": canonical_json_text(WorkspaceManifest()),
        workflow_source_path(spec.id): serialize_workflow_spec(spec),
    }
    transport = AsyncMock()
    transport.read_files.return_value = VcsTreeSnapshot(
        commit_sha="a" * 40,
        tree_sha="tree-sha",
        files=files,
    )
    import_service = AsyncMock()
    import_service.validate_workflows.return_value = []
    workspace_sync_service._workspace_git_url = AsyncMock(
        return_value=GitUrl(host="github.com", org="tracecat", repo="sync")
    )
    workspace_sync_service._resolve_local_workflow_id = AsyncMock(
        return_value=WorkflowUUID.new_uuid4()
    )
    workspace_sync_service._upsert_mappings = AsyncMock()

    with (
        patch(
            "tracecat.workspace_sync.service.vcs_transport_for_provider",
            return_value=transport,
        ),
        patch(
            "tracecat.workspace_sync.service.WorkflowImportService",
            return_value=import_service,
        ),
    ):
        result = await workspace_sync_service.pull(
            options=PullOptions(commit_sha="a" * 40),
        )

    assert result.success is True
    import_service.validate_workflows.assert_awaited_once()
    import_service.import_workflows.assert_awaited_once()
    assert import_service.import_workflows.call_args.kwargs["sync_schedules"] is False


@pytest.mark.anyio
async def test_pull_dry_run_returns_resource_diffs_without_import(
    workspace_sync_service: WorkspaceSyncService,
    sample_dsl: DSLInput,
) -> None:
    source_id = "sync-me"
    current_dsl = sample_dsl.model_copy(update={"title": "Current title"})
    incoming_dsl = sample_dsl.model_copy(update={"title": "Incoming title"})
    current_spec = WorkflowResourceSpec(
        id=source_id,
        alias="sync-me",
        definition=current_dsl,
    )
    incoming_spec = WorkflowResourceSpec(
        id=source_id,
        alias="sync-me",
        definition=incoming_dsl,
    )
    current_files = _workspace_files(current_spec)
    incoming_files = _workspace_files(incoming_spec)
    transport = AsyncMock()
    transport.read_files.return_value = VcsTreeSnapshot(
        commit_sha="a" * 40,
        tree_sha="tree-sha",
        files=incoming_files,
    )
    import_service = AsyncMock()
    import_service.validate_workflows.return_value = []
    workspace_sync_service._workspace_git_url = AsyncMock(
        return_value=GitUrl(host="github.com", org="tracecat", repo="sync")
    )
    workspace_sync_service._resolve_local_workflow_id = AsyncMock(
        return_value=WorkflowUUID.new_uuid4()
    )
    workspace_sync_service.project_workspace = AsyncMock(
        return_value=WorkspaceProjection(
            manifest=WorkspaceManifest(),
            spec=WorkspaceSpec(workflows={source_id: current_spec}),
            files=current_files,
        )
    )

    with (
        patch(
            "tracecat.workspace_sync.service.vcs_transport_for_provider",
            return_value=transport,
        ),
        patch(
            "tracecat.workspace_sync.service.WorkflowImportService",
            return_value=import_service,
        ),
    ):
        result = await workspace_sync_service.pull(
            options=PullOptions(commit_sha="a" * 40, dry_run=True),
        )

    assert result.success is True
    assert result.workflows_imported == 0
    assert result.resource_diffs is not None
    assert len(result.resource_diffs) == 1
    assert result.files == sorted(incoming_files)
    assert result.resources is not None
    assert [(resource.name, resource.path) for resource in result.resources] == [
        ("sync-me", workflow_source_path(source_id))
    ]
    diff = result.resource_diffs[0]
    assert diff.change_type == "modified"
    assert diff.resource_type == SyncResourceType.WORKFLOW.value
    assert diff.source_id == source_id
    assert diff.title == "Incoming title"
    assert "Current title" in diff.diff
    assert "Incoming title" in diff.diff
    import_service.validate_workflows.assert_awaited_once()
    import_service.import_workflows.assert_not_awaited()


@pytest.mark.anyio
async def test_pull_dry_run_projects_current_workspace_by_target_resource_types(
    workspace_sync_service: WorkspaceSyncService,
) -> None:
    source_id = "default/api_token"
    current_spec = WorkspaceSpec(
        variables={
            source_id: VariableResourceSpec(
                id=source_id,
                name="api_token",
                environment="default",
                description="current",
            )
        }
    )
    incoming_spec = WorkspaceSpec(
        variables={
            source_id: VariableResourceSpec(
                id=source_id,
                name="api_token",
                environment="default",
                description="incoming",
            )
        }
    )
    transport = AsyncMock()
    transport.read_files.return_value = VcsTreeSnapshot(
        commit_sha="a" * 40,
        tree_sha="tree-sha",
        files=workspace_sync_service._files_from_spec(
            manifest=WorkspaceManifest(),
            spec=incoming_spec,
        ),
    )
    import_service = AsyncMock()
    import_service.validate_workflows.return_value = []
    workspace_sync_service._workspace_git_url = AsyncMock(
        return_value=GitUrl(host="github.com", org="tracecat", repo="sync")
    )
    workspace_sync_service.project_workspace = AsyncMock(
        return_value=WorkspaceProjection(
            manifest=WorkspaceManifest(),
            spec=current_spec,
            files=workspace_sync_service._files_from_spec(
                manifest=WorkspaceManifest(),
                spec=current_spec,
            ),
        )
    )

    with (
        patch(
            "tracecat.workspace_sync.service.vcs_transport_for_provider",
            return_value=transport,
        ),
        patch(
            "tracecat.workspace_sync.service.WorkflowImportService",
            return_value=import_service,
        ),
    ):
        result = await workspace_sync_service.pull(
            options=PullOptions(commit_sha="a" * 40, dry_run=True),
        )

    assert result.success is True
    assert result.resource_diffs is not None
    assert len(result.resource_diffs) == 1
    diff = result.resource_diffs[0]
    assert diff.resource_type == SyncResourceType.VARIABLE.value
    assert diff.title == "api_token"
    assert "current" in diff.diff
    assert "incoming" in diff.diff
    workspace_sync_service.project_workspace.assert_awaited_once()
    await_args = workspace_sync_service.project_workspace.await_args
    assert await_args is not None
    _, kwargs = await_args
    assert kwargs["create_missing_mappings"] is False
    assert kwargs["resource_ids"] == {SyncResourceType.VARIABLE: set()}
    import_service.import_workflows.assert_not_awaited()


@pytest.mark.anyio
async def test_pull_dry_run_ignores_schedule_diff_when_schedule_sync_is_disabled(
    workspace_sync_service: WorkspaceSyncService,
    sample_dsl: DSLInput,
) -> None:
    source_id = "sync-me"
    current_spec = WorkflowResourceSpec(
        id=source_id,
        alias="sync-me",
        definition=sample_dsl,
    )
    incoming_spec = current_spec.model_copy(
        update={"schedules": [RemoteWorkflowSchedule(cron="0 8 * * *")]}
    )
    current_files = _workspace_files(current_spec)
    incoming_files = _workspace_files(incoming_spec)
    transport = AsyncMock()
    transport.read_files.return_value = VcsTreeSnapshot(
        commit_sha="a" * 40,
        tree_sha="tree-sha",
        files=incoming_files,
    )
    import_service = AsyncMock()
    import_service.validate_workflows.return_value = []
    workspace_sync_service._workspace_git_url = AsyncMock(
        return_value=GitUrl(host="github.com", org="tracecat", repo="sync")
    )
    workspace_sync_service._resolve_local_workflow_id = AsyncMock(
        return_value=WorkflowUUID.new_uuid4()
    )
    workspace_sync_service.project_workspace = AsyncMock(
        return_value=WorkspaceProjection(
            manifest=WorkspaceManifest(),
            spec=WorkspaceSpec(workflows={source_id: current_spec}),
            files=current_files,
        )
    )

    with (
        patch(
            "tracecat.workspace_sync.service.vcs_transport_for_provider",
            return_value=transport,
        ),
        patch(
            "tracecat.workspace_sync.service.WorkflowImportService",
            return_value=import_service,
        ),
    ):
        result = await workspace_sync_service.pull(
            options=PullOptions(commit_sha="a" * 40, dry_run=True),
            sync_schedules=False,
        )

    assert result.success is True
    assert result.resource_diffs == []


@pytest.mark.anyio
async def test_parse_files_accepts_legacy_workflow_tree_without_manifest(
    workspace_sync_service: WorkspaceSyncService,
) -> None:
    source_id = WorkflowUUID.new_uuid4().short()

    snapshot, diagnostics = await workspace_sync_service.parse_files(
        {
            workflow_source_path(source_id): _legacy_workflow_yaml(
                source_id,
                title="Legacy workflow",
            )
        }
    )

    assert diagnostics == []
    assert list(snapshot.spec.workflows) == [source_id]
    assert snapshot.spec.workflows[source_id].alias == "legacy-workflow"


@pytest.mark.anyio
async def test_parse_files_accepts_legacy_string_version_manifest(
    workspace_sync_service: WorkspaceSyncService,
) -> None:
    source_id = WorkflowUUID.new_uuid4().short()

    snapshot, diagnostics = await workspace_sync_service.parse_files(
        {
            MANIFEST_FILENAME: '{"version":"1"}',
            workflow_source_path(source_id): _legacy_workflow_yaml(
                source_id,
                title="Legacy manifest workflow",
            ),
        }
    )

    assert diagnostics == []
    assert list(snapshot.spec.workflows) == [source_id]


@pytest.mark.anyio
async def test_parse_files_accepts_nested_workflow_manifest_root(
    workspace_sync_service: WorkspaceSyncService,
    sample_dsl: DSLInput,
) -> None:
    source_id = "qa-nested"
    workflow_spec = WorkflowResourceSpec(
        id=source_id,
        alias="qa-nested",
        definition=sample_dsl,
    )
    manifest = WorkspaceManifest(
        resources=WorkspaceManifestResources(workflows="sync/workflows/")
    )

    snapshot, diagnostics = await workspace_sync_service.parse_files(
        {
            MANIFEST_FILENAME: canonical_json_text(manifest),
            f"sync/workflows/{source_id}/definition.yml": serialize_workflow_spec(
                workflow_spec
            ),
        }
    )

    assert diagnostics == []
    assert list(snapshot.spec.workflows) == [source_id]
    assert snapshot.spec.workflows[source_id].alias == "qa-nested"


@pytest.mark.anyio
async def test_resource_ref_without_ids_selects_resource_type(
    workspace_sync_service: WorkspaceSyncService,
) -> None:
    resource_ids = await workspace_sync_service._local_ids_from_resource_refs(
        [
            ResourceRef(resource_type=SyncResourceType.AGENT_PRESET),
            ResourceRef(resource_type=SyncResourceType.SKILL),
        ]
    )

    assert resource_ids == {
        SyncResourceType.AGENT_PRESET: set(),
        SyncResourceType.SKILL: set(),
    }


def test_table_names_collects_explicit_table_reference_fields() -> None:
    payloads = [
        {
            "args": {
                "table": "qa_indicators",
                "nested": [
                    {"table_name": "case_context"},
                    {"table_slug": "enrichment_cache"},
                ],
            }
        }
    ]

    assert _table_names(payloads) == {
        "case_context",
        "enrichment_cache",
        "qa_indicators",
    }


def test_table_names_ignores_free_form_string_tokens() -> None:
    payloads = [
        {
            "title": "Escalate customers affected by Okta alert",
            "args": {
                "prompt": (
                    "Find customers mentioned in this alert and compare them "
                    "against qa_indicators."
                )
            },
        }
    ]

    assert _table_names(payloads) == set()


@pytest.mark.anyio
async def test_preview_export_counts_resources_without_mutating_mappings(
    workspace_sync_service: WorkspaceSyncService,
    sample_dsl: DSLInput,
) -> None:
    spec = WorkspaceSpec(
        workflows={
            "wf-1": WorkflowResourceSpec(
                id="wf-1", alias="wf-1", definition=sample_dsl
            ),
            "wf-2": WorkflowResourceSpec(
                id="wf-2", alias="wf-2", definition=sample_dsl
            ),
        }
    )
    workspace_sync_service.project_workspace = AsyncMock(
        return_value=WorkspaceProjection(
            manifest=WorkspaceManifest(),
            spec=spec,
            files={"b.json": "{}", "a.json": "{}"},
        )
    )

    preview = await workspace_sync_service.preview_export_workspace(
        WorkspaceSyncExportPreviewRequest(
            resources=[ResourceRef(resource_type=SyncResourceType.WORKFLOW)],
        )
    )

    assert preview.resource_counts[SyncResourceType.WORKFLOW.value] == 2
    assert preview.resource_counts[SyncResourceType.AGENT_PRESET.value] == 0
    # Files come back sorted for a stable preview payload.
    assert preview.files == ["a.json", "b.json"]
    assert [
        (resource.resource_type, resource.source_id, resource.name, resource.path)
        for resource in preview.resources
    ] == [
        (
            SyncResourceType.WORKFLOW,
            "wf-1",
            "wf-1",
            "workflows/wf-1/definition.yml",
        ),
        (
            SyncResourceType.WORKFLOW,
            "wf-2",
            "wf-2",
            "workflows/wf-2/definition.yml",
        ),
    ]
    # Preview must never create sync mappings as a side effect.
    workspace_sync_service.project_workspace.assert_awaited_once()
    await_args = workspace_sync_service.project_workspace.await_args
    assert await_args is not None
    _, kwargs = await_args
    assert kwargs["create_missing_mappings"] is False
    assert kwargs["resource_ids"] == {SyncResourceType.WORKFLOW: set()}


@pytest.mark.anyio
async def test_preview_export_reports_resource_diffs_against_compare_ref(
    workspace_sync_service: WorkspaceSyncService,
    sample_dsl: DSLInput,
) -> None:
    fake_vcs = FakeVcsServer()
    git_url = GitUrl(host="github.com", org="TracecatHQ", repo="sync")
    seed_transport = fake_vcs.transport_factory(
        VcsProvider.GITHUB,
        session=workspace_sync_service.session,
        role=workspace_sync_service.role,
    )
    await seed_transport.write_files(
        url=git_url,
        files={
            MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
            "workflows/remove/definition.yml": "version: 1\n",
            "workflows/stale/definition.yml": "version: 1\n",
            "README.md": "# keep me\n",
        },
        message="Seed sync branch",
        branch="sync/workspace",
        create_pr=False,
    )
    service = WorkspaceSyncService(
        session=workspace_sync_service.session,
        role=workspace_sync_service.role,
        transport_factory=fake_vcs.transport_factory,
    )
    service._workspace_git_url = AsyncMock(return_value=git_url)
    service.project_workspace = AsyncMock(
        return_value=WorkspaceProjection(
            manifest=WorkspaceManifest(),
            spec=WorkspaceSpec(
                workflows={
                    "fresh": WorkflowResourceSpec(
                        id="fresh",
                        alias="fresh",
                        definition=sample_dsl,
                    ),
                    "stale": WorkflowResourceSpec(
                        id="stale",
                        alias="stale",
                        definition=sample_dsl,
                    ),
                }
            ),
            files={
                MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
                "workflows/fresh/definition.yml": "version: 1\n",
                "workflows/stale/definition.yml": "version: 2\n",
            },
        )
    )

    preview = await service.preview_export_workspace(
        WorkspaceSyncExportPreviewRequest(compare_ref="sync/workspace")
    )

    assert [
        (diff.change_type, diff.source_path, diff.title)
        for diff in preview.resource_diffs
    ] == [
        ("added", "workflows/fresh/definition.yml", "Sync me"),
        ("deleted", "workflows/remove/definition.yml", None),
        ("modified", "workflows/stale/definition.yml", "Sync me"),
    ]
    assert "README.md" not in {diff.source_path for diff in preview.resource_diffs}
    assert "+version: 2" in preview.resource_diffs[2].diff
    assert "-version: 1" in preview.resource_diffs[1].diff


@pytest.mark.anyio
async def test_preview_export_reports_type_wide_deletion_diffs(
    workspace_sync_service: WorkspaceSyncService,
) -> None:
    fake_vcs = FakeVcsServer()
    git_url = GitUrl(host="github.com", org="TracecatHQ", repo="sync")
    seed_transport = fake_vcs.transport_factory(
        VcsProvider.GITHUB,
        session=workspace_sync_service.session,
        role=workspace_sync_service.role,
    )
    await seed_transport.write_files(
        url=git_url,
        files={
            MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
            "tables/stale/table.yml": "version: 1\n",
            "workflows/stale/definition.yml": "version: 1\n",
        },
        message="Seed sync branch",
        branch="sync/workspace",
        create_pr=False,
    )
    service = WorkspaceSyncService(
        session=workspace_sync_service.session,
        role=workspace_sync_service.role,
        transport_factory=fake_vcs.transport_factory,
    )
    service._workspace_git_url = AsyncMock(return_value=git_url)
    service.project_workspace = AsyncMock(
        return_value=WorkspaceProjection(
            manifest=WorkspaceManifest(),
            spec=WorkspaceSpec(),
            files={MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest())},
        )
    )

    preview = await service.preview_export_workspace(
        WorkspaceSyncExportPreviewRequest(
            resources=[ResourceRef(resource_type=SyncResourceType.TABLE)],
            compare_ref="sync/workspace",
        )
    )

    assert [
        (diff.change_type, diff.source_path, diff.title)
        for diff in preview.resource_diffs
    ] == [("deleted", "tables/stale/table.yml", None)]


@pytest.mark.anyio
async def test_preview_export_requires_scopes_for_projected_sensitive_metadata() -> (
    None
):
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        scopes=frozenset({"workspace_sync:sync"}),
    )
    service = WorkspaceSyncService(session=AsyncMock(), role=role)
    service.project_workspace = AsyncMock(
        return_value=WorkspaceProjection(
            manifest=WorkspaceManifest(),
            spec=WorkspaceSpec(
                variables={
                    "default/api_token": VariableResourceSpec(
                        id="default/api_token",
                        name="api_token",
                        environment="default",
                    )
                },
                secret_metadata={
                    "default/vendor_api": SecretMetadataResourceSpec(
                        id="default/vendor_api",
                        name="vendor_api",
                        environment="default",
                        keys=["TOKEN"],
                    )
                },
            ),
            files={},
        )
    )

    with pytest.raises(ScopeDeniedError) as exc_info:
        await service.preview_export_workspace(WorkspaceSyncExportPreviewRequest())

    assert set(exc_info.value.missing_scopes) == {"secret:read", "variable:read"}


def test_workflow_export_scope_accepts_legacy_or_workspace_sync_grant() -> None:
    base_role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        scopes=frozenset({"workflow:update", "workflow:sync"}),
    )
    service = WorkspaceSyncService(session=AsyncMock(), role=base_role)

    service._require_workflow_export_scope()

    service.role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=base_role.workspace_id,
        organization_id=base_role.organization_id,
        scopes=frozenset({"workflow:update", "workspace_sync:sync"}),
    )
    service._require_workflow_export_scope()

    service.role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=base_role.workspace_id,
        organization_id=base_role.organization_id,
        scopes=frozenset({"workflow:update"}),
    )
    with pytest.raises(ScopeDeniedError):
        service._require_workflow_export_scope()


@pytest.mark.anyio
async def test_workflow_export_allows_projected_dependencies_with_publish_scope(
    sample_dsl: DSLInput,
) -> None:
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        scopes=frozenset({"workflow:update", "workflow:sync"}),
    )
    session = AsyncMock()
    session.add = Mock()
    service = WorkspaceSyncService(session=session, role=role)
    workflow_id = WorkflowUUID.new_uuid4()
    workflow = cast(Any, SimpleNamespace(id=workflow_id, git_sync_branch=None))
    projection_spec = WorkspaceSpec(
        workflows={
            "parent": WorkflowResourceSpec(
                id="parent",
                alias="parent",
                definition=sample_dsl,
            )
        },
        variables={
            "default/api_token": VariableResourceSpec(
                id="default/api_token",
                name="api_token",
                environment="default",
            )
        },
        secret_metadata={
            "default/vendor_api": SecretMetadataResourceSpec(
                id="default/vendor_api",
                name="vendor_api",
                environment="default",
                keys=["TOKEN"],
            )
        },
    )
    projection_manifest = WorkspaceManifest()
    projection = WorkspaceProjection(
        manifest=projection_manifest,
        spec=projection_spec,
        files=service._files_from_spec(
            manifest=projection_manifest,
            spec=projection_spec,
        ),
    )
    service._workspace_git_url = AsyncMock(
        return_value=GitUrl(host="github.com", org="tracecat", repo="sync")
    )
    service.project_workspace = AsyncMock(return_value=projection)
    transport = AsyncMock()
    transport.write_files.return_value = CommitInfo(
        status=PushStatus.COMMITTED,
        sha="a" * 40,
        ref="sync/workflow",
        base_ref="main",
        pr_url=None,
        pr_number=None,
        pr_reused=False,
        message="Committed workspace sync changes.",
    )

    with patch(
        "tracecat.workspace_sync.service.vcs_transport_for_provider",
        return_value=transport,
    ):
        result = await service.export_workflow(
            workflow=workflow,
            dsl=sample_dsl,
            params=WorkspaceSyncExportRequest(
                message="Publish workflow",
                branch="sync/workflow",
                create_pr=False,
            ),
        )

    assert result.commit.status is PushStatus.COMMITTED
    transport.write_files.assert_awaited_once()
    expected_paths = {
        MANIFEST_FILENAME,
        workflow_source_path("parent"),
        VARIABLE_RESOURCE_ADAPTER.source_path("default/api_token"),
        SECRET_METADATA_RESOURCE_ADAPTER.source_path("default/vendor_api"),
    }
    written_files = transport.write_files.await_args.kwargs["files"]
    assert set(written_files) == expected_paths
    assert set(result.files) == expected_paths
    assert workflow.git_sync_branch == "sync/workflow"


def test_sync_operation_scope_accepts_legacy_or_workspace_sync_grant() -> None:
    base_role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        scopes=frozenset({"workflow:sync"}),
    )
    service = WorkspaceSyncService(session=AsyncMock(), role=base_role)

    service._require_sync_operation_scope()

    service.role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=base_role.workspace_id,
        organization_id=base_role.organization_id,
        scopes=frozenset({"workspace_sync:sync"}),
    )
    service._require_sync_operation_scope()

    service.role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=base_role.workspace_id,
        organization_id=base_role.organization_id,
        scopes=frozenset(),
    )
    with pytest.raises(ScopeDeniedError):
        service._require_sync_operation_scope()


@pytest.mark.anyio
async def test_preview_export_rejects_missing_pinned_skill_version(
    workspace_sync_service: WorkspaceSyncService,
) -> None:
    workspace_sync_service.project_workspace = AsyncMock(
        return_value=WorkspaceProjection(
            manifest=WorkspaceManifest(),
            spec=WorkspaceSpec(
                agent_presets={
                    "qa-triage": AgentPresetResourceSpec(
                        id="qa-triage",
                        slug="qa-triage",
                        name="QA triage",
                        current_version=1,
                        versions={
                            1: AgentPresetVersionResourceSpec(
                                version_number=1,
                                name="QA triage",
                                skills=[
                                    AgentPresetSkillBinding(
                                        slug="qa-enrichment-skill",
                                        version=1,
                                    )
                                ],
                            )
                        },
                    )
                },
                skills={
                    "qa-enrichment-skill": SkillResourceSpec(
                        id="qa-enrichment-skill",
                        slug="qa-enrichment-skill",
                        name="QA enrichment skill",
                        current_version=2,
                    )
                },
            ),
            files={},
        )
    )

    with pytest.raises(TracecatValidationError, match="missing skill version"):
        await workspace_sync_service.preview_export_workspace(
            WorkspaceSyncExportPreviewRequest()
        )


@pytest.mark.anyio
async def test_pull_dry_run_requires_read_scopes_for_incoming_resources() -> None:
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        scopes=frozenset({"workspace_sync:sync"}),
    )
    service = WorkspaceSyncService(session=AsyncMock(), role=role)
    incoming_spec = WorkspaceSpec(
        variables={
            "default/api_token": VariableResourceSpec(
                id="default/api_token",
                name="api_token",
                environment="default",
            )
        },
        secret_metadata={
            "default/vendor_api": SecretMetadataResourceSpec(
                id="default/vendor_api",
                name="vendor_api",
                environment="default",
                keys=["TOKEN"],
            )
        },
    )
    transport = AsyncMock()
    transport.read_files.return_value = VcsTreeSnapshot(
        commit_sha="a" * 40,
        tree_sha="tree-sha",
        files=service._files_from_spec(
            manifest=WorkspaceManifest(),
            spec=incoming_spec,
        ),
    )
    service._workspace_git_url = AsyncMock(
        return_value=GitUrl(host="github.com", org="tracecat", repo="sync")
    )
    service.project_workspace = AsyncMock()

    with (
        patch(
            "tracecat.workspace_sync.service.vcs_transport_for_provider",
            return_value=transport,
        ),
        pytest.raises(ScopeDeniedError) as exc_info,
    ):
        await service.pull(options=PullOptions(commit_sha="a" * 40, dry_run=True))

    assert set(exc_info.value.missing_scopes) == {"secret:read", "variable:read"}
    service.project_workspace.assert_not_awaited()


@pytest.mark.anyio
async def test_pull_apply_requires_create_scopes_for_incoming_resources() -> None:
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        scopes=frozenset({"workspace_sync:sync", "secret:update", "variable:update"}),
    )
    service = WorkspaceSyncService(session=AsyncMock(), role=role)
    incoming_spec = WorkspaceSpec(
        variables={
            "default/api_token": VariableResourceSpec(
                id="default/api_token",
                name="api_token",
                environment="default",
            )
        },
        secret_metadata={
            "default/vendor_api": SecretMetadataResourceSpec(
                id="default/vendor_api",
                name="vendor_api",
                environment="default",
                keys=["TOKEN"],
            )
        },
    )
    transport = AsyncMock()
    transport.read_files.return_value = VcsTreeSnapshot(
        commit_sha="a" * 40,
        tree_sha="tree-sha",
        files=service._files_from_spec(
            manifest=WorkspaceManifest(),
            spec=incoming_spec,
        ),
    )
    service._workspace_git_url = AsyncMock(
        return_value=GitUrl(host="github.com", org="tracecat", repo="sync")
    )

    with (
        patch(
            "tracecat.workspace_sync.service.vcs_transport_for_provider",
            return_value=transport,
        ),
        pytest.raises(ScopeDeniedError) as exc_info,
    ):
        await service.pull(options=PullOptions(commit_sha="a" * 40))

    assert set(exc_info.value.missing_scopes) == {"secret:create", "variable:create"}


@pytest.mark.anyio
async def test_pull_blocks_entitled_resource_without_entitlement() -> None:
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        scopes=frozenset({"workspace_sync:sync", "case:update"}),
    )
    service = WorkspaceSyncService(session=AsyncMock(), role=role)
    service.has_entitlement = AsyncMock(return_value=False)
    incoming_spec = WorkspaceSpec(
        case_dropdowns={
            "resolution_reason": CaseDropdownResourceSpec(
                id="resolution_reason",
                name="Resolution reason",
            )
        },
    )
    transport = AsyncMock()
    transport.read_files.return_value = VcsTreeSnapshot(
        commit_sha="a" * 40,
        tree_sha="tree-sha",
        files=service._files_from_spec(
            manifest=WorkspaceManifest(),
            spec=incoming_spec,
        ),
    )
    service._workspace_git_url = AsyncMock(
        return_value=GitUrl(host="github.com", org="tracecat", repo="sync")
    )

    with (
        patch(
            "tracecat.workspace_sync.service.vcs_transport_for_provider",
            return_value=transport,
        ),
        pytest.raises(EntitlementRequired) as exc_info,
    ):
        await service.pull(options=PullOptions(commit_sha="a" * 40))

    assert exc_info.value.entitlement == "case_addons"


@pytest.mark.anyio
async def test_pull_allows_workflow_case_trigger_without_entitlement(
    sample_dsl: DSLInput,
) -> None:
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        scopes=frozenset({"workspace_sync:sync", "workflow:read"}),
    )
    service = WorkspaceSyncService(session=AsyncMock(), role=role)
    service.has_entitlement = AsyncMock(return_value=False)
    service._resource_diffs_for_pull = AsyncMock(return_value=[])
    service._validate_workflow_import = AsyncMock(return_value=[])
    incoming_spec = WorkspaceSpec(
        workflows={
            "case-workflow": WorkflowResourceSpec(
                id="case-workflow",
                alias="case-workflow",
                case_trigger=RemoteCaseTrigger(
                    status="online",
                    event_types=[CaseEventType.CASE_CREATED],
                    tag_filters=[],
                ),
                definition=sample_dsl,
            )
        },
    )
    transport = AsyncMock()
    transport.read_files.return_value = VcsTreeSnapshot(
        commit_sha="a" * 40,
        tree_sha="tree-sha",
        files=service._files_from_spec(
            manifest=WorkspaceManifest(),
            spec=incoming_spec,
        ),
    )
    service._workspace_git_url = AsyncMock(
        return_value=GitUrl(host="github.com", org="tracecat", repo="sync")
    )

    with patch(
        "tracecat.workspace_sync.service.vcs_transport_for_provider",
        return_value=transport,
    ):
        result = await service.pull(
            options=PullOptions(commit_sha="a" * 40, dry_run=True)
        )

    assert result.success is True
    assert result.resource_counts is not None
    assert result.resource_counts[SyncResourceType.WORKFLOW].found == 1
    assert result.resource_counts[SyncResourceType.WORKFLOW].imported == 0


@pytest.mark.anyio
async def test_full_export_skips_unentitled_resource_types() -> None:
    service = WorkspaceSyncService(
        session=AsyncMock(),
        role=Role(
            type="service",
            service_id="tracecat-api",
            workspace_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            scopes=frozenset({"workspace_sync:sync"}),
        ),
    )
    service.has_entitlement = AsyncMock(return_value=False)

    resource_types = await service._entitled_non_workflow_types(
        full_workspace_export=True,
        selection=None,
    )

    assert SyncResourceType.CASE_DROPDOWN not in resource_types
    assert SyncResourceType.CASE_DURATION not in resource_types
    assert SyncResourceType.CASE_FIELD in resource_types


@pytest.mark.anyio
async def test_selected_export_blocks_unentitled_resource_type() -> None:
    service = WorkspaceSyncService(
        session=AsyncMock(),
        role=Role(
            type="service",
            service_id="tracecat-api",
            workspace_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            scopes=frozenset({"workspace_sync:sync"}),
        ),
    )
    service.has_entitlement = AsyncMock(return_value=False)

    with pytest.raises(EntitlementRequired) as exc_info:
        await service._entitled_non_workflow_types(
            full_workspace_export=False,
            selection={SyncResourceType.CASE_DROPDOWN: set()},
        )

    assert exc_info.value.entitlement == "case_addons"


@pytest.mark.anyio
async def test_selected_workflow_export_includes_workflow_id_children(
    workspace_sync_service: WorkspaceSyncService,
    sample_dsl: DSLInput,
) -> None:
    parent_id = WorkflowUUID.new_uuid4()
    child_id = WorkflowUUID.new_uuid4()
    parent = cast(Any, SimpleNamespace(id=parent_id, alias=None))
    child = cast(Any, SimpleNamespace(id=child_id, alias=None))
    parent_dsl = DSLInput(
        title="Parent",
        description="Calls a child workflow by id",
        entrypoint=DSLEntrypoint(ref="run_child", expects={}),
        actions=[
            ActionStatement(
                ref="run_child",
                action="core.workflow.execute",
                args={"workflow_id": child_id.short()},
            )
        ],
    )

    async def get_workflow_dsl(workflow: Any, **_: Any) -> DSLInput:
        return parent_dsl if workflow.id == parent_id else sample_dsl

    get_workflow_dsl_mock = AsyncMock(side_effect=get_workflow_dsl)
    with (
        patch.object(
            workspace_sync_service,
            "_list_projectable_workflows",
            AsyncMock(return_value=[parent, child]),
        ),
        patch.object(
            workspace_sync_service,
            "_get_workflow_dsl",
            get_workflow_dsl_mock,
        ),
    ):
        closure = await workspace_sync_service._projectable_workflow_closure(
            {SyncResourceType.WORKFLOW: {parent_id}}
        )

    assert [workflow.id for workflow in closure.workflows] == [parent_id, child_id]
    assert closure.dsl_by_id == {parent_id: parent_dsl, child_id: sample_dsl}
    assert get_workflow_dsl_mock.await_count == 2


def test_workflow_spec_to_remote_rewrites_child_workflow_id_references() -> None:
    source_parent_id = WorkflowUUID.new_uuid4()
    source_child_id = WorkflowUUID.new_uuid4()
    local_parent_id = WorkflowUUID.new_uuid4()
    local_child_id = WorkflowUUID.new_uuid4()
    spec = WorkflowResourceSpec(
        id=source_parent_id.short(),
        alias="parent",
        definition=DSLInput(
            title="Parent",
            description="Calls a child workflow by source id",
            entrypoint=DSLEntrypoint(ref="run_child", expects={}),
            actions=[
                ActionStatement(
                    ref="run_child",
                    action="core.workflow.execute",
                    args={"workflow_id": source_child_id.short()},
                )
            ],
        ),
    )

    remote = workflow_spec_to_remote(
        spec,
        local_workflow_id=local_parent_id,
        local_workflow_ids={
            source_parent_id.short(): local_parent_id,
            source_child_id.short(): local_child_id,
        },
    )

    assert remote.definition.actions[0].args["workflow_id"] == local_child_id.short()
    assert spec.definition.actions[0].args["workflow_id"] == source_child_id.short()


def test_workflow_spec_export_rewrites_local_child_workflow_id_references() -> None:
    local_child_id = WorkflowUUID.new_uuid4()
    spec = WorkflowResourceSpec(
        id="parent",
        alias="parent",
        definition=DSLInput(
            title="Parent",
            description="Calls a child workflow by local id",
            entrypoint=DSLEntrypoint(ref="run_child", expects={}),
            actions=[
                ActionStatement(
                    ref="run_child",
                    action="core.workflow.execute",
                    args={"workflow_id": local_child_id.short()},
                )
            ],
        ),
    )

    exported = workflow_spec_with_source_workflow_ids(
        spec,
        source_workflow_ids={local_child_id: "child"},
    )

    assert exported.definition.actions[0].args["workflow_id"] == "child"
    assert spec.definition.actions[0].args["workflow_id"] == local_child_id.short()


@pytest.mark.anyio
async def test_export_workspace_commits_mapping_changes(
    workspace_sync_service: WorkspaceSyncService,
) -> None:
    projection = WorkspaceProjection(
        manifest=WorkspaceManifest(),
        spec=WorkspaceSpec(),
        files={MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest())},
    )
    transport = AsyncMock()
    transport.write_files.return_value = CommitInfo(
        status=PushStatus.COMMITTED,
        sha="b" * 40,
        ref="sync/workspace",
        base_ref="main",
        pr_url=None,
        pr_number=None,
        pr_reused=False,
        message="Committed workspace sync changes.",
    )
    workspace_sync_service._workspace_git_url = AsyncMock(
        return_value=GitUrl(host="github.com", org="TracecatHQ", repo="sync")
    )
    workspace_sync_service.project_workspace = AsyncMock(return_value=projection)

    with patch(
        "tracecat.workspace_sync.service.vcs_transport_for_provider",
        return_value=transport,
    ):
        result = await workspace_sync_service.export_workspace(
            WorkspaceSyncExportRequest(
                message="Push workspace",
                branch="sync/workspace",
                create_pr=True,
            )
        )

    assert result.files == [MANIFEST_FILENAME]
    cast(AsyncMock, workspace_sync_service.session.commit).assert_awaited_once()


@pytest.mark.anyio
async def test_gitlab_export_uses_gitlab_transport_and_mapping_context(
    workspace_sync_service: WorkspaceSyncService,
) -> None:
    projection = WorkspaceProjection(
        manifest=WorkspaceManifest(),
        spec=WorkspaceSpec(),
        files={MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest())},
    )
    transport = AsyncMock()
    transport.write_files.return_value = CommitInfo(
        status=PushStatus.COMMITTED,
        sha="c" * 40,
        ref="sync/workspace",
        base_ref="main",
        pr_url=None,
        pr_number=None,
        pr_reused=False,
        message="Committed workspace sync changes.",
    )
    providers_seen: list[VcsProvider] = []
    service = WorkspaceSyncService(
        session=workspace_sync_service.session,
        role=workspace_sync_service.role,
        provider=VcsProvider.GITLAB,
    )

    async def workspace_git_url(*, provider: VcsProvider) -> GitUrl:
        providers_seen.append(provider)
        return GitUrl(
            host="gitlab.example.test",
            org="TracecatHQ/platform",
            repo="sync",
        )

    async def project_workspace(**_kwargs: Any) -> WorkspaceProjection:
        # The provider is fixed at construction, so mapping reads stay on GitLab.
        assert service._mapping_provider is VcsProvider.GITLAB
        return projection

    def transport_factory(
        provider: VcsProvider,
        *,
        session: Any,
        role: Any,
    ) -> Any:
        del session, role
        providers_seen.append(provider)
        return transport

    service._workspace_git_url = workspace_git_url
    service.project_workspace = project_workspace
    service._transport_factory = transport_factory

    result = await service.export_workspace(
        WorkspaceSyncExportRequest(
            message="Push workspace",
            branch="sync/workspace",
            create_pr=True,
            provider=VcsProvider.GITLAB,
        )
    )

    assert result.files == [MANIFEST_FILENAME]
    assert providers_seen == [VcsProvider.GITLAB, VcsProvider.GITLAB]
    assert service._mapping_provider is VcsProvider.GITLAB


@pytest.mark.anyio
async def test_full_export_deletes_stale_sync_files(
    workspace_sync_service: WorkspaceSyncService,
) -> None:
    fake_vcs = FakeVcsServer()
    git_url = GitUrl(host="github.com", org="TracecatHQ", repo="sync")
    await _seed_fake_sync_branch(
        fake_vcs,
        git_url=git_url,
        branch="sync/workspace",
    )
    service = WorkspaceSyncService(
        session=workspace_sync_service.session,
        role=workspace_sync_service.role,
        transport_factory=fake_vcs.transport_factory,
    )
    service._workspace_git_url = AsyncMock(return_value=git_url)
    service.project_workspace = AsyncMock(
        return_value=WorkspaceProjection(
            manifest=WorkspaceManifest(),
            spec=WorkspaceSpec(),
            files={MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest())},
        )
    )

    result = await service.export_workspace(
        WorkspaceSyncExportRequest(
            message="Push full workspace",
            branch="sync/workspace",
            create_pr=False,
        )
    )

    assert result.commit.status is PushStatus.COMMITTED
    assert result.commit.sha is not None
    files = fake_vcs.repo_files(git_url, ref=result.commit.sha)
    assert "workflows/stale/definition.yml" not in files
    assert files["README.md"] == "# keep me\n"


@pytest.mark.anyio
async def test_selected_export_preserves_unselected_sync_files(
    workspace_sync_service: WorkspaceSyncService,
) -> None:
    fake_vcs = FakeVcsServer()
    git_url = GitUrl(host="github.com", org="TracecatHQ", repo="sync")
    await _seed_fake_sync_branch(
        fake_vcs,
        git_url=git_url,
        branch="sync/workspace",
    )
    service = WorkspaceSyncService(
        session=workspace_sync_service.session,
        role=workspace_sync_service.role,
        transport_factory=fake_vcs.transport_factory,
    )
    service._workspace_git_url = AsyncMock(return_value=git_url)
    service.project_workspace = AsyncMock(
        return_value=WorkspaceProjection(
            manifest=WorkspaceManifest(),
            spec=WorkspaceSpec(),
            files={MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest())},
        )
    )

    result = await service.export_workspace(
        WorkspaceSyncExportRequest(
            message="Push selected tables",
            branch="sync/workspace",
            create_pr=False,
            resources=[ResourceRef(resource_type=SyncResourceType.TABLE)],
        )
    )

    assert result.commit.status is PushStatus.NO_OP
    files = fake_vcs.repo_files(git_url, ref="sync/workspace")
    assert "workflows/stale/definition.yml" in files


@pytest.mark.anyio
async def test_type_wide_export_deletes_stale_files_for_selected_resource_type(
    workspace_sync_service: WorkspaceSyncService,
) -> None:
    fake_vcs = FakeVcsServer()
    git_url = GitUrl(host="github.com", org="TracecatHQ", repo="sync")
    seed_transport = fake_vcs.transport_factory(
        VcsProvider.GITHUB,
        session=AsyncMock(),
        role=AsyncMock(),
    )
    await seed_transport.write_files(
        url=git_url,
        files={
            MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
            "tables/stale/table.yml": "version: 1\n",
            "workflows/stale/definition.yml": "version: 1\n",
        },
        message="Seed sync branch",
        branch="sync/workspace",
        create_pr=False,
    )
    service = WorkspaceSyncService(
        session=workspace_sync_service.session,
        role=workspace_sync_service.role,
        transport_factory=fake_vcs.transport_factory,
    )
    service._workspace_git_url = AsyncMock(return_value=git_url)
    service.project_workspace = AsyncMock(
        return_value=WorkspaceProjection(
            manifest=WorkspaceManifest(),
            spec=WorkspaceSpec(),
            files={MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest())},
        )
    )

    result = await service.export_workspace(
        WorkspaceSyncExportRequest(
            message="Push selected tables",
            branch="sync/workspace",
            create_pr=False,
            resources=[ResourceRef(resource_type=SyncResourceType.TABLE)],
        )
    )

    assert result.commit.status is PushStatus.COMMITTED
    assert result.commit.sha is not None
    files = fake_vcs.repo_files(git_url, ref=result.commit.sha)
    assert "tables/stale/table.yml" not in files
    assert "workflows/stale/definition.yml" in files


@pytest.mark.anyio
async def test_selected_export_deletes_stale_companion_files_under_selected_resource(
    workspace_sync_service: WorkspaceSyncService,
) -> None:
    fake_vcs = FakeVcsServer()
    git_url = GitUrl(host="github.com", org="TracecatHQ", repo="sync")
    seed_transport = fake_vcs.transport_factory(
        VcsProvider.GITHUB,
        session=AsyncMock(),
        role=AsyncMock(),
    )
    await seed_transport.write_files(
        url=git_url,
        files={
            MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
            "skills/qa-enrichment-skill/skill.yml": "version: 1\n",
            "skills/qa-enrichment-skill/versions/1.yml": "old version\n",
            "skills/unselected-skill/skill.yml": "version: 1\n",
        },
        message="Seed sync branch",
        branch="sync/workspace",
        create_pr=False,
    )
    service = WorkspaceSyncService(
        session=workspace_sync_service.session,
        role=workspace_sync_service.role,
        transport_factory=fake_vcs.transport_factory,
    )
    service._workspace_git_url = AsyncMock(return_value=git_url)
    service.project_workspace = AsyncMock(
        return_value=WorkspaceProjection(
            manifest=WorkspaceManifest(),
            spec=WorkspaceSpec(
                skills={
                    "qa-enrichment-skill": SkillResourceSpec(
                        id="qa-enrichment-skill",
                        slug="qa-enrichment-skill",
                        name="QA enrichment skill",
                    )
                }
            ),
            files={
                MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
                "skills/qa-enrichment-skill/skill.yml": "version: 1\nname: current\n",
            },
        )
    )

    result = await service.export_workspace(
        WorkspaceSyncExportRequest(
            message="Push selected skill",
            branch="sync/workspace",
            create_pr=False,
            resources=[ResourceRef(resource_type=SyncResourceType.SKILL)],
        )
    )

    assert result.commit.status is PushStatus.COMMITTED
    assert result.commit.sha is not None
    files = fake_vcs.repo_files(git_url, ref=result.commit.sha)
    assert "skills/qa-enrichment-skill/versions/1.yml" not in files
    assert "skills/unselected-skill/skill.yml" in files


@pytest.mark.anyio
async def test_github_write_files_noop_skips_pr_for_branch_without_commits(
    workspace_sync_service: WorkspaceSyncService,
) -> None:
    files = {MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest())}
    repo = _FakeGitHubRepo(
        files=files,
        branch_exists=False,
        ahead_by=0,
    )

    result = await _write_files_with_fake_repo(
        repo,
        service=workspace_sync_service,
        files=files,
    )

    assert result.status is PushStatus.NO_OP
    assert result.pr_url is None
    assert repo.created_refs == [("refs/heads/sync/agents-1", "a" * 40)]
    assert repo.compare_calls == [("main", "sync/agents-1")]
    repo.create_pull.assert_not_called()


@pytest.mark.anyio
async def test_github_write_files_noop_reuses_existing_pr_for_branch_with_commits(
    workspace_sync_service: WorkspaceSyncService,
) -> None:
    files = {MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest())}
    repo = _FakeGitHubRepo(
        files=files,
        branch_exists=True,
        ahead_by=1,
        existing_pr=SimpleNamespace(
            html_url="https://github.com/TracecatHQ/sync/pull/7",
            number=7,
        ),
    )

    result = await _write_files_with_fake_repo(
        repo,
        service=workspace_sync_service,
        files=files,
    )

    assert result.status is PushStatus.NO_OP
    assert result.pr_url == "https://github.com/TracecatHQ/sync/pull/7"
    assert result.pr_number == 7
    assert result.pr_reused is True
    assert repo.compare_calls == [("main", "sync/agents-1")]


@pytest.mark.anyio
async def test_github_write_files_rejects_pr_for_base_branch(
    workspace_sync_service: WorkspaceSyncService,
) -> None:
    files = {MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest())}
    repo = _FakeGitHubRepo(
        files={},
        branch_exists=True,
        ahead_by=0,
    )

    with pytest.raises(TracecatValidationError, match="non-base branch"):
        await _write_files_with_fake_repo(
            repo,
            service=workspace_sync_service,
            files=files,
            branch="main",
        )

    assert repo.blobs == []
    assert repo.trees == []
    assert repo.commits == []
    repo.create_pull.assert_not_called()


@pytest.mark.anyio
async def test_github_write_files_uses_tree_sha_for_stale_path_scan(
    workspace_sync_service: WorkspaceSyncService,
) -> None:
    files = {MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest())}
    repo = _FakeGitHubRepo(
        files={
            MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
            "workflows/stale/definition.yml": "version: 1\n",
        },
        branch_exists=True,
        ahead_by=0,
    )

    result = await _write_files_with_fake_repo(
        repo,
        service=workspace_sync_service,
        files=files,
        create_pr=False,
        delete_missing_paths_under=("workflows",),
    )

    assert result.status is PushStatus.COMMITTED
    assert repo.get_git_tree_calls == [f"tree-{'a' * 40}"]
    assert len(repo.trees) == 1


@pytest.mark.anyio
async def test_github_read_files_uses_commit_tree_sha(
    workspace_sync_service: WorkspaceSyncService,
) -> None:
    repo = _FakeGitHubReadRepo()
    gh = Mock()
    gh.get_repo.return_value = repo
    gh_service = AsyncMock()
    gh_service.get_github_client_for_repo.return_value = gh

    transport = GitHubWorkspaceSyncTransport(
        session=workspace_sync_service.session,
        role=workspace_sync_service.role,
    )
    with patch(
        "tracecat.workspace_sync.transport.GitHubAppService",
        return_value=gh_service,
    ):
        snapshot = await transport.read_files(
            url=GitUrl(host="github.com", org="TracecatHQ", repo="sync"),
            ref="c" * 40,
        )

    # The git/trees endpoint must receive the commit's tree SHA, not the commit
    # SHA, or GitHub 404s the read.
    assert repo.get_git_tree_calls == ["t" * 40]
    assert snapshot.commit_sha == "c" * 40
    assert snapshot.tree_sha == "t" * 40
    assert MANIFEST_FILENAME in snapshot.files


def test_bitbucket_transport_is_explicitly_unsupported() -> None:
    error = unsupported_transport(VcsProvider.BITBUCKET)

    assert isinstance(error, TracecatValidationError)
    assert VcsProvider.BITBUCKET.value in str(error)


def _legacy_workflow_yaml(source_id: str, *, title: str) -> str:
    return f"""
id: {source_id}
alias: legacy-workflow
definition:
  title: {title}
  description: Legacy workspace sync format
  entrypoint:
    ref: start
    expects: {{}}
  actions:
    - ref: start
      action: core.transform.passthrough
      args:
        value: legacy
"""


def _workspace_files(spec: WorkflowResourceSpec) -> dict[str, str]:
    return {
        MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
        workflow_source_path(spec.id): serialize_workflow_spec(spec),
    }


async def _seed_fake_sync_branch(
    fake_vcs: FakeVcsServer,
    *,
    git_url: GitUrl,
    branch: str,
) -> None:
    seed_transport = fake_vcs.transport_factory(
        VcsProvider.GITHUB,
        session=AsyncMock(),
        role=AsyncMock(),
    )
    await seed_transport.write_files(
        url=git_url,
        files={
            MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
            "workflows/stale/definition.yml": "version: 1\n",
            "README.md": "# keep me\n",
        },
        message="Seed sync branch",
        branch=branch,
        create_pr=False,
    )


async def _write_files_with_fake_repo(
    repo: _FakeGitHubRepo,
    *,
    service: WorkspaceSyncService,
    files: dict[str, str],
    branch: str = "sync/agents-1",
    create_pr: bool = True,
    delete_missing_paths_under: tuple[str, ...] = (),
):
    gh = Mock()
    gh.get_repo.return_value = repo

    gh_service = AsyncMock()
    gh_service.get_github_client_for_repo.return_value = gh

    transport = GitHubWorkspaceSyncTransport(
        session=service.session,
        role=service.role,
    )
    with patch(
        "tracecat.workspace_sync.transport.GitHubAppService",
        return_value=gh_service,
    ):
        return await transport.write_files(
            url=GitUrl(host="github.com", org="TracecatHQ", repo="sync"),
            files=files,
            message="Push limerick agent",
            branch=branch,
            create_pr=create_pr,
            delete_missing_paths_under=delete_missing_paths_under,
        )


class _FakeGitHubRepo:
    default_branch = "main"

    def __init__(
        self,
        *,
        files: dict[str, str],
        branch_exists: bool,
        ahead_by: int,
        existing_pr: object | None = None,
    ) -> None:
        self._files = files
        self._branch_exists = branch_exists
        self._ahead_by = ahead_by
        self._existing_pr = existing_pr
        self.created_refs: list[tuple[str, str]] = []
        self.compare_calls: list[tuple[str, str]] = []
        self.create_pull = Mock(
            return_value=SimpleNamespace(
                html_url="https://github.com/TracecatHQ/sync/pull/8",
                number=8,
            )
        )
        self.blobs: list[tuple[str, str]] = []
        self.trees: list[object] = []
        self.commits: list[object] = []
        self.get_git_tree_calls: list[str] = []

    def get_branch(self, name: str):
        if name == "main" or self._branch_exists:
            return SimpleNamespace(commit=SimpleNamespace(sha="a" * 40))
        raise GithubException(status=404, data={"message": "Not Found"})

    def create_git_ref(self, *, ref: str, sha: str) -> None:
        self.created_refs.append((ref, sha))
        self._branch_exists = True

    def get_contents(self, path: str, *, ref: str):
        if path not in self._files:
            raise GithubException(status=404, data={"message": "Not Found"})
        encoded = base64.b64encode(self._files[path].encode()).decode()
        return SimpleNamespace(content=encoded)

    def compare(self, base: str, head: str):
        self.compare_calls.append((base, head))
        return SimpleNamespace(ahead_by=self._ahead_by)

    def get_git_commit(self, sha: str):
        return SimpleNamespace(tree=SimpleNamespace(sha=f"tree-{sha}"))

    def get_git_tree(self, *, sha: str, recursive: bool):
        self.get_git_tree_calls.append(sha)
        return SimpleNamespace(
            tree=[
                SimpleNamespace(path=path, type="blob") for path in sorted(self._files)
            ]
        )

    def create_git_blob(self, content: str, encoding: str):
        self.blobs.append((content, encoding))
        return SimpleNamespace(sha=f"blob-{len(self.blobs)}")

    def create_git_tree(self, elements: list[object], *, base_tree: object):
        tree = SimpleNamespace(elements=elements, base_tree=base_tree)
        self.trees.append(tree)
        return tree

    def create_git_commit(self, message: str, tree: object, parents: list[object]):
        commit = SimpleNamespace(
            sha=f"commit-{len(self.commits) + 1}",
            message=message,
            tree=tree,
            parents=parents,
        )
        self.commits.append(commit)
        return commit

    def get_git_ref(self, ref: str):
        return _FakeGitRef(ref)

    def get_pulls(self, *, state: str, head: str, base: str):
        if self._existing_pr is None:
            return iter(())
        return iter((self._existing_pr,))


class _FakeGitRef:
    def __init__(self, ref: str) -> None:
        self.ref = ref
        self.edits: list[str] = []

    def edit(self, *, sha: str) -> None:
        self.edits.append(sha)


class _FakeGitHubReadRepo:
    """Minimal GitHub repo whose commit SHA and root tree SHA differ."""

    def __init__(self) -> None:
        self.get_git_tree_calls: list[str] = []

    def get_commit(self, ref: str):
        return SimpleNamespace(
            sha="c" * 40,
            commit=SimpleNamespace(tree=SimpleNamespace(sha="t" * 40)),
        )

    def get_git_tree(self, *, sha: str, recursive: bool):
        self.get_git_tree_calls.append(sha)
        return SimpleNamespace(
            tree=[
                SimpleNamespace(
                    path=MANIFEST_FILENAME,
                    sha="blob-manifest",
                    type="blob",
                )
            ]
        )

    def get_git_blob(self, sha: str):
        content = canonical_json_text(WorkspaceManifest())
        return SimpleNamespace(content=base64.b64encode(content.encode()).decode())
