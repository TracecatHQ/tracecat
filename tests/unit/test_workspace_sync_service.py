"""Tests for simple workspace sync service behavior."""

from __future__ import annotations

import base64
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from github.GithubException import GithubException

from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.schemas import ActionStatement
from tracecat.exceptions import TracecatValidationError
from tracecat.git.types import GitUrl
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.sync import PullOptions, PushStatus
from tracecat.workflow.store.schemas import RemoteWorkflowSchedule
from tracecat.workspace_sync.enums import SyncResourceType, VcsProvider
from tracecat.workspace_sync.schemas import (
    MANIFEST_FILENAME,
    ResourceRef,
    WorkflowResourceSpec,
    WorkspaceManifest,
    WorkspaceProjection,
    WorkspaceSpec,
    WorkspaceSyncExportPreviewRequest,
)
from tracecat.workspace_sync.serialization import canonical_json_text
from tracecat.workspace_sync.service import WorkspaceSyncService
from tracecat.workspace_sync.transport import (
    GitHubWorkspaceSyncTransport,
    VcsTreeSnapshot,
    unsupported_transport,
)
from tracecat.workspace_sync.workflow import (
    serialize_workflow_spec,
    workflow_source_path,
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
    workspace_sync_service._upsert_mapping = AsyncMock()

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
    # Preview must never create sync mappings as a side effect.
    workspace_sync_service.project_workspace.assert_awaited_once()
    await_args = workspace_sync_service.project_workspace.await_args
    assert await_args is not None
    _, kwargs = await_args
    assert kwargs["create_missing_mappings"] is False
    assert kwargs["resource_ids"] == {SyncResourceType.WORKFLOW: set()}


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


def test_gitlab_and_bitbucket_transports_are_explicitly_unsupported() -> None:
    for provider in (VcsProvider.GITLAB, VcsProvider.BITBUCKET):
        error = unsupported_transport(provider)
        assert isinstance(error, TracecatValidationError)
        assert provider.value in str(error)


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


async def _write_files_with_fake_repo(
    repo: _FakeGitHubRepo,
    *,
    service: WorkspaceSyncService,
    files: dict[str, str],
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
            branch="sync/agents-1",
            create_pr=True,
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

    def get_pulls(self, *, state: str, head: str, base: str):
        if self._existing_pr is None:
            return iter(())
        return iter((self._existing_pr,))
