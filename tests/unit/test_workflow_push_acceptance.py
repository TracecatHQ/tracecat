"""Database-backed first-push behavior using an in-memory Git provider."""

from unittest.mock import AsyncMock, patch

import pytest
import yaml
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.support.fake_vcs import FakeVcsServer
from tracecat.auth.types import Role
from tracecat.db.models import (
    Action,
    Schedule,
    Workflow,
    WorkflowDefinition,
    Workspace,
    WorkspaceSyncResourceMapping,
)
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.workflow.store.export_definitions import WorkflowExportDefinitionService
from tracecat.workflow.store.router import publish_workflow
from tracecat.workflow.store.schemas import WorkflowDslPublish, WorkflowDslPublishResult
from tracecat.workspace_sync.enums import SyncResourceType, VcsProvider
from tracecat.workspace_sync.exceptions import WorkflowExportValidationError
from tracecat.workspace_sync.schemas import (
    ResourceRef,
    WorkspaceSyncExportPreviewRequest,
    WorkspaceSyncExportRequest,
)
from tracecat.workspace_sync.service import WorkspaceSyncService


async def _workflow(
    session: AsyncSession,
    role: Role,
    title: str,
    *,
    alias: str,
    child: str | None = None,
    empty: bool = False,
) -> Workflow:
    workflow = Workflow(
        workspace_id=role.workspace_id,
        title=title,
        description="Synthetic Git export fixture",
        alias=alias,
    )
    session.add(workflow)
    await session.flush()
    if not empty:
        session.add(
            Action(
                workspace_id=role.workspace_id,
                workflow_id=workflow.id,
                title="Start",
                description="Synthetic action",
                type="core.workflow.execute" if child else "core.transform.passthrough",
                inputs=f"workflow_alias: {child}" if child else "value: example",
            )
        )
        await session.flush()
    return workflow


@pytest.mark.anyio
async def test_first_workflow_push_builds_content_and_children_without_publishing(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    workspace = await session.scalar(
        select(Workspace).where(Workspace.id == svc_role.workspace_id)
    )
    assert workspace is not None
    workspace.settings = {
        **(workspace.settings or {}),
        "git_repo_url": "git+ssh://git@github.com/example/synthetic-sync.git",
    }
    child = await _workflow(session, svc_role, "Example child", alias="example-child")
    parent = await _workflow(
        session,
        svc_role,
        "Example parent",
        alias="example-parent",
        child="example-child",
    )
    schedule = Schedule(
        workspace_id=svc_role.workspace_id,
        workflow_id=parent.id,
        status="offline",
        cron="0 9 * * *",
        inputs={"example": "value"},
    )
    session.add(schedule)
    await session.commit()
    vcs = FakeVcsServer()
    sync = WorkspaceSyncService(
        session=session, role=svc_role, transport_factory=vcs.transport_factory
    )
    # Exercise the actual workflow route, resolver, store, projection, and Git
    # transport. Only replace the external provider with the in-memory server.
    with patch(
        "tracecat.workflow.store.service.WorkspaceSyncService.for_workspace",
        AsyncMock(return_value=sync),
    ):
        result = await publish_workflow(
            role=svc_role,
            session=session,
            workflow_id=WorkflowUUID.new(parent.id),
            params=WorkflowDslPublish(branch="sync/first-push", create_pr=False),
        )
    assert isinstance(result, WorkflowDslPublishResult)
    assert result.status == "committed"
    assert parent.git_sync_branch == "sync/first-push"
    assert parent.version is None
    assert child.version is None
    assert (
        await session.scalar(
            select(func.count())
            .select_from(WorkflowDefinition)
            .where(WorkflowDefinition.workspace_id == svc_role.workspace_id)
        )
        == 0
    )
    mappings = list(
        (
            await session.scalars(
                select(WorkspaceSyncResourceMapping).where(
                    WorkspaceSyncResourceMapping.workspace_id == svc_role.workspace_id,
                    WorkspaceSyncResourceMapping.resource_type == "workflow",
                )
            )
        ).all()
    )
    assert {mapping.local_id for mapping in mappings} == {parent.id, child.id}
    preview = await sync.preview_export_workspace(
        WorkspaceSyncExportPreviewRequest(
            resources=[
                ResourceRef(resource_type=SyncResourceType.WORKFLOW, local_id=parent.id)
            ]
        )
    )
    assert preview.resource_counts["workflow"] == 2
    projection = await sync.project_workspace(
        workflow_ids=[WorkflowUUID.new(parent.id)], create_missing_mappings=False
    )
    exported = [
        yaml.safe_load(content)
        for path, content in projection.files.items()
        if path.endswith("definition.yml")
    ]
    assert {item["definition"]["title"] for item in exported} == {
        "Example parent",
        "Example child",
    }
    assert all("schedules" not in item for item in exported)
    await session.refresh(schedule)
    assert schedule.status == "offline"
    assert schedule.cron == "0 9 * * *"
    assert schedule.inputs == {"example": "value"}

    saved_content = next(
        item["definition"]
        for item in exported
        if item["definition"]["title"] == "Example parent"
    )
    session.add(
        WorkflowDefinition(
            workspace_id=svc_role.workspace_id,
            workflow_id=parent.id,
            version=1,
            content=saved_content,
        )
    )
    parent.version = 1
    parent.title = "Different current edits"
    await session.commit()
    with patch(
        "tracecat.workflow.store.service.WorkspaceSyncService.for_workspace",
        AsyncMock(return_value=sync),
    ):
        saved_result = await publish_workflow(
            role=svc_role,
            session=session,
            workflow_id=WorkflowUUID.new(parent.id),
            params=WorkflowDslPublish(branch="sync/saved-content", create_pr=False),
        )
    assert isinstance(saved_result, WorkflowDslPublishResult)
    assert saved_result.status == "committed"
    saved_projection = await sync.project_workspace(
        workflow_ids=[WorkflowUUID.new(parent.id)], create_missing_mappings=False
    )
    saved_exports = [
        yaml.safe_load(content)
        for path, content in saved_projection.files.items()
        if path.endswith("definition.yml")
    ]
    assert {item["definition"]["title"] for item in saved_exports} == {
        "Example parent",
        "Example child",
    }
    assert parent.version == 1
    assert (
        await session.scalar(
            select(func.count())
            .select_from(WorkflowDefinition)
            .where(WorkflowDefinition.workspace_id == svc_role.workspace_id)
        )
        == 1
    )
    await session.refresh(schedule)
    assert schedule.status == "offline"
    assert schedule.cron == "0 9 * * *"


@pytest.mark.anyio
async def test_preview_and_push_report_all_invalid_workflows_without_writes(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    workspace = await session.scalar(
        select(Workspace).where(Workspace.id == svc_role.workspace_id)
    )
    assert workspace is not None
    workspace.settings = {
        **(workspace.settings or {}),
        "git_repo_url": "git+ssh://git@github.com/example/synthetic-sync.git",
    }
    valid = await _workflow(session, svc_role, "Example valid", alias="example-valid")
    invalid = [
        await _workflow(
            session,
            svc_role,
            "Example review",
            alias=f"example-empty-{index}",
            empty=True,
        )
        for index in range(2)
    ]
    await session.commit()
    transport = AsyncMock()
    sync = WorkspaceSyncService(
        session=session,
        role=svc_role,
        transport_factory=lambda *_args, **_kwargs: transport,
    )
    errors = []
    for operation in [
        sync.preview_export_workspace(WorkspaceSyncExportPreviewRequest()),
        sync.export_workspace(
            WorkspaceSyncExportRequest(
                message="Synthetic sync", branch="sync/validation"
            )
        ),
    ]:
        with pytest.raises(WorkflowExportValidationError) as raised:
            await operation
        errors.append(raised.value)
        assert {item.workflow_id for item in raised.value.diagnostics} == {
            workflow.id for workflow in invalid
        }
        assert all(item.source_path is None for item in raised.value.diagnostics)
        assert (
            await session.scalar(
                select(func.count())
                .select_from(WorkspaceSyncResourceMapping)
                .where(
                    WorkspaceSyncResourceMapping.workspace_id == svc_role.workspace_id
                )
            )
            == 0
        )
        transport.write_files.assert_not_awaited()
    assert [item.model_dump() for item in errors[0].diagnostics] == [
        item.model_dump() for item in errors[1].diagnostics
    ]
    assert valid.git_sync_branch is None


@pytest.mark.anyio
async def test_export_diagnostic_uses_only_current_provider_mapping(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    workflow = await _workflow(
        session, svc_role, "Example empty", alias="example-empty", empty=True
    )
    workspace = await session.scalar(
        select(Workspace).where(Workspace.id == svc_role.workspace_id)
    )
    assert workspace is not None
    workspace.settings = {
        **(workspace.settings or {}),
        "git_provider": VcsProvider.GITLAB,
    }
    for provider in ["github", "gitlab"]:
        session.add(
            WorkspaceSyncResourceMapping(
                workspace_id=svc_role.workspace_id,
                provider=provider,
                resource_type="workflow",
                local_id=workflow.id,
                source_id=f"example-{provider}",
                source_path=f"workflows/example-{provider}/definition.yml",
            )
        )
    await session.commit()
    with pytest.raises(WorkflowExportValidationError) as raised:
        await WorkflowExportDefinitionService(session=session, role=svc_role).resolve(
            workflow
        )
    diagnostic = raised.value.diagnostics[0]
    assert diagnostic.source_id == "example-gitlab"
    assert diagnostic.source_path == "workflows/example-gitlab/definition.yml"
