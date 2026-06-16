"""Tests for simple workspace sync service behavior."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, Mock, patch

import pytest

from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.schemas import ActionStatement
from tracecat.exceptions import TracecatValidationError
from tracecat.git.types import GitUrl
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.sync import PullOptions
from tracecat.workspace_sync.enums import SyncResourceType, VcsProvider
from tracecat.workspace_sync.schemas import (
    MANIFEST_FILENAME,
    ResourceRef,
    WorkflowResourceSpec,
    WorkspaceManifest,
)
from tracecat.workspace_sync.serialization import canonical_json_text
from tracecat.workspace_sync.service import WorkspaceSyncService
from tracecat.workspace_sync.transport import (
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
