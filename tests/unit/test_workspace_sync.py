"""Tests for workspace Git sync projection primitives."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
import yaml
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.cases.enums import CaseEventType
from tracecat.db.models import (
    Workflow,
    WorkflowDefinition,
    WorkspaceSyncChangeSet,
    WorkspaceSyncChangeSetItem,
    WorkspaceSyncResourceMapping,
)
from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.schemas import ActionStatement
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.registry.lock.types import RegistryLock
from tracecat.sync import CommitInfo, PushStatus
from tracecat.workflow.management.definitions import WorkflowDefinitionsService
from tracecat.workflow.management.management import WorkflowsManagementService
from tracecat.workflow.store.schemas import RemoteWorkflowDefinition
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.git import GitTreeSnapshot
from tracecat.workspace_sync.schemas import (
    ChangeSetCreate,
    ChangeSetExport,
    ResourceRef,
    WorkspaceManifest,
)
from tracecat.workspace_sync.serialization import canonical_json_text, stable_hash
from tracecat.workspace_sync.service import WorkspaceGitSyncService
from tracecat.workspace_sync.workflow import (
    parse_workflow_spec,
    serialize_workflow_spec,
    workflow_spec_from_orm,
)


@pytest.fixture
def sample_dsl() -> DSLInput:
    return DSLInput(
        title="Detect Okta Risk",
        description="Detects suspicious Okta activity",
        entrypoint=DSLEntrypoint(ref="start", expects={}),
        actions=[
            ActionStatement(
                ref="start",
                action="core.transform.passthrough",
                args={"value": "test"},
            )
        ],
    )


def test_manifest_serializes_as_canonical_json() -> None:
    text = canonical_json_text(WorkspaceManifest())

    assert (
        text
        == '{\n  "resources": {\n    "workflows": "workflows/"\n  },\n  "version": 1\n}\n'
    )


def test_stable_hash_ignores_model_defaults() -> None:
    class HashModel(BaseModel):
        name: str
        future_default: str = "default"

    model_hash = stable_hash(HashModel(name="workflow"))

    assert model_hash.startswith("v1:")
    assert model_hash == stable_hash({"name": "workflow"})


def test_workflow_spec_does_not_serialize_local_uuid(sample_dsl: DSLInput) -> None:
    local_id = uuid.uuid4()
    workflow = SimpleNamespace(
        id=local_id,
        alias="okta-risk",
        tags=[],
        folder=None,
        schedules=[],
        webhook=SimpleNamespace(
            methods=["POST"], status="online", include_headers=False
        ),
        case_trigger=SimpleNamespace(
            status="offline",
            event_types=[],
            tag_filters=[],
        ),
    )

    spec = workflow_spec_from_orm(
        cast(Workflow, workflow),
        dsl=sample_dsl,
        source_id="detect-okta-risk",
    )
    content = serialize_workflow_spec(spec)

    assert "detect-okta-risk" in content
    assert str(local_id) not in content
    assert "wf_" not in content


def test_workflow_spec_includes_configured_case_trigger(sample_dsl: DSLInput) -> None:
    workflow = SimpleNamespace(
        id=uuid.uuid4(),
        alias="okta-risk",
        tags=[],
        folder=None,
        schedules=[],
        webhook=SimpleNamespace(
            methods=["POST"], status="online", include_headers=False
        ),
        case_trigger=SimpleNamespace(
            status="online",
            event_types=[CaseEventType.CASE_CREATED.value],
            tag_filters=["phishing"],
        ),
    )

    spec = workflow_spec_from_orm(
        cast(Workflow, workflow),
        dsl=sample_dsl,
        source_id="detect-okta-risk",
    )

    assert spec.case_trigger is not None
    assert spec.case_trigger.status == "online"
    assert spec.case_trigger.event_types == [CaseEventType.CASE_CREATED]
    assert spec.case_trigger.tag_filters == ["phishing"]


def test_legacy_workflow_file_dual_reads_to_source_id(
    sample_dsl: DSLInput,
) -> None:
    legacy = RemoteWorkflowDefinition(
        id="wf_0000000000000000000001",
        alias="legacy-workflow",
        definition=sample_dsl,
    )
    content = yaml.safe_dump(
        legacy.model_dump(mode="json", exclude_none=True),
        sort_keys=False,
    )

    spec, diagnostic = parse_workflow_spec(
        "workflows/legacy-source/definition.yml",
        content,
    )

    assert diagnostic is None
    assert spec is not None
    assert spec.id == "legacy-source"
    assert spec.alias == "legacy-workflow"


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_resource_mapping_stores_source_id_to_local_uuid(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = WorkspaceGitSyncService(session=session, role=svc_role)
    local_id = uuid.uuid4()

    mapping = await service._ensure_resource_mapping(
        resource_type=SyncResourceType.WORKFLOW.value,
        local_id=local_id,
        preferred_source_id="detect-okta-risk",
        source_path="workflows/detect-okta-risk/definition.yml",
        create=True,
        reserved_source_ids=set(),
    )

    assert mapping is not None
    assert mapping.source_id == "detect-okta-risk"
    assert mapping.local_id == local_id
    assert mapping.workspace_id == svc_role.workspace_id


class FakeGitHubSyncTransport:
    files: dict[str, str] = {}
    written_files: dict[str, str] | None = None
    written_branch: str | None = None
    written_create_pr: bool | None = None

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def read_files(self, *args, **kwargs) -> GitTreeSnapshot:
        return GitTreeSnapshot(
            commit_sha="a" * 40,
            tree_sha="b" * 40,
            files=self.files,
        )

    async def write_files(
        self,
        *,
        files: dict[str, str],
        branch: str,
        create_pr: bool,
        **kwargs,
    ) -> CommitInfo:
        self.__class__.written_files = files
        self.__class__.written_branch = branch
        self.__class__.written_create_pr = create_pr
        return CommitInfo(
            status=PushStatus.COMMITTED,
            sha="c" * 40,
            ref=branch,
            base_ref=kwargs.get("pr_base_branch") or "main",
            pr_url="https://github.com/test-org/test-repo/pull/1"
            if create_pr
            else None,
            pr_number=1 if create_pr else None,
            pr_reused=False,
            message="Committed workspace sync changes.",
        )


async def _create_local_workflow(
    *,
    session: AsyncSession,
    role: Role,
    dsl: DSLInput,
    alias: str,
) -> Workflow:
    with patch(
        "tracecat.workflow.management.management.RegistryLockService.resolve_lock_with_bindings",
        new=AsyncMock(
            return_value=RegistryLock(
                origins={"tracecat_registry": "test"},
                actions={"core.transform.passthrough": "tracecat_registry"},
            )
        ),
    ):
        workflow = await WorkflowsManagementService(
            session=session,
            role=role,
        ).create_db_workflow_from_dsl(
            dsl,
            workflow_alias=alias,
            commit=False,
        )
    await WorkflowDefinitionsService(
        session=session,
        role=role,
    ).create_workflow_definition(
        WorkflowUUID.new(workflow.id),
        dsl,
        alias=alias,
        commit=False,
    )
    await session.commit()
    return workflow


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_status_pending_changeset_and_export_with_mocked_github(
    session: AsyncSession,
    svc_role: Role,
    svc_workspace,
    sample_dsl: DSLInput,
) -> None:
    svc_workspace.settings = {
        "git_repo_url": "git+ssh://git@github.com/test-org/test-repo.git"
    }
    session.add(svc_workspace)
    await _create_local_workflow(
        session=session,
        role=svc_role,
        dsl=sample_dsl,
        alias="detect-okta-risk",
    )

    FakeGitHubSyncTransport.files = {}
    FakeGitHubSyncTransport.written_files = None
    FakeGitHubSyncTransport.written_branch = None
    FakeGitHubSyncTransport.written_create_pr = None

    with patch(
        "tracecat.workspace_sync.service.WorkspaceGitHubSyncService",
        FakeGitHubSyncTransport,
    ):
        service = WorkspaceGitSyncService(session=session, role=svc_role)

        status = await service.get_status()
        assert status.status == "never_synced"
        assert status.pending_change_count == 1
        assert status.remote_commit_sha == "a" * 40

        pending = await service.list_pending_changes()
        assert len(pending.changes) == 1
        pending_change = pending.changes[0]
        assert pending_change.operation == "create"
        assert pending_change.source_id == "detect-okta-risk"
        assert pending_change.title == "Detect Okta Risk"
        mapping_before_changeset = await session.scalar(
            select(WorkspaceSyncResourceMapping).where(
                WorkspaceSyncResourceMapping.workspace_id == svc_role.workspace_id
            )
        )
        assert mapping_before_changeset is None

        changeset = await service.create_changeset(
            params=ChangeSetCreate(
                title="Export workflow",
                resources=[
                    ResourceRef(
                        resource_type=pending_change.resource_type,
                        source_id=pending_change.source_id,
                        source_path=pending_change.source_path,
                    )
                ],
            )
        )
        assert changeset.status == "validated"
        assert changeset.selected_paths == [
            "tracecat.json",
            "workflows/detect-okta-risk/definition.yml",
        ]
        assert changeset.selected_resources[0]["local_id"] is not None
        changeset_row = await session.scalar(
            select(WorkspaceSyncChangeSet).where(
                WorkspaceSyncChangeSet.id == changeset.id
            )
        )
        assert changeset_row is not None
        assert set(changeset_row.rendered_files) == {
            "tracecat.json",
            "workflows/detect-okta-risk/definition.yml",
        }

        changeset_item = await session.scalar(
            select(WorkspaceSyncChangeSetItem).where(
                WorkspaceSyncChangeSetItem.changeset_id == changeset.id
            )
        )
        assert changeset_item is not None
        assert changeset_item.operation == "create"
        assert changeset_item.local_id is not None

        definition = await session.scalar(
            select(WorkflowDefinition).where(
                WorkflowDefinition.workspace_id == svc_role.workspace_id,
                WorkflowDefinition.alias == "detect-okta-risk",
            )
        )
        assert definition is not None
        definition.content = {**definition.content, "title": "Mutated Okta Risk"}
        session.add(definition)
        await session.commit()

        result = await service.export_changeset(
            changeset_id=changeset.id,
            params=ChangeSetExport(
                message="Export workflow",
                branch="sync/detect-okta-risk",
                create_pr=True,
            ),
        )

    assert result.commit.status == PushStatus.COMMITTED
    assert result.commit.pr_number == 1
    assert FakeGitHubSyncTransport.written_branch == "sync/detect-okta-risk"
    assert FakeGitHubSyncTransport.written_create_pr is True
    assert FakeGitHubSyncTransport.written_files is not None
    assert set(FakeGitHubSyncTransport.written_files) == {
        "tracecat.json",
        "workflows/detect-okta-risk/definition.yml",
    }
    written_workflow = yaml.safe_load(
        FakeGitHubSyncTransport.written_files[
            "workflows/detect-okta-risk/definition.yml"
        ]
    )
    assert written_workflow["definition"]["title"] == "Detect Okta Risk"
