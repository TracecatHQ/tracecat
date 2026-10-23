"""Database-backed first-push behavior using an in-memory Git provider."""

from unittest.mock import AsyncMock, patch

import pytest
import yaml
from fastapi.responses import JSONResponse
from sqlalchemy import event, func, select
from sqlalchemy.engine import Connection, ExecutionContext
from sqlalchemy.ext.asyncio import AsyncSession

from tests.support.fake_vcs import FakeVcsServer, FakeVcsTransport
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
from tracecat.workflow.management.definitions import WorkflowDefinitionsService
from tracecat.workflow.store.router import publish_workflow
from tracecat.workflow.store.schemas import WorkflowDslPublish, WorkflowDslPublishResult
from tracecat.workspace_sync.enums import SyncResourceType, VcsProvider
from tracecat.workspace_sync.exceptions import WorkflowExportValidationError
from tracecat.workspace_sync.schemas import (
    ResourceRef,
    WorkspaceSyncExportError,
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
async def test_workspace_push_exports_current_edits_without_publishing(
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
    statements: list[str] = []

    def capture_sql(
        _connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: ExecutionContext,
        _executemany: bool,
    ) -> None:
        statements.append(statement.lstrip().split(maxsplit=1)[0].upper())

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", capture_sql)
    try:
        preview = await sync.preview_export_workspace(
            WorkspaceSyncExportPreviewRequest(
                resources=[
                    ResourceRef(
                        resource_type=SyncResourceType.WORKFLOW, local_id=parent.id
                    )
                ]
            )
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture_sql)
    assert preview.resource_counts["workflow"] == 2
    assert not {"INSERT", "UPDATE", "DELETE"}.intersection(statements)
    assert (
        await session.scalar(
            select(func.count()).select_from(WorkspaceSyncResourceMapping)
        )
        == 0
    )
    assert (
        await session.scalar(select(func.count()).select_from(WorkflowDefinition)) == 0
    )
    # Workspace Push keeps its existing unpublished-content fallback.
    result = await sync.export_workspace(
        WorkspaceSyncExportRequest(
            message="Synthetic workspace push",
            branch="sync/first-push",
            create_pr=False,
            resources=[
                ResourceRef(resource_type=SyncResourceType.WORKFLOW, local_id=parent.id)
            ],
        )
    )
    assert result.commit.status == "committed"
    assert parent.git_sync_branch is None
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
        sync = await WorkspaceSyncService.for_workspace(session=session, role=svc_role)
        await sync._get_workflow_dsl(workflow)
    diagnostic = raised.value.diagnostics[0]
    assert diagnostic.source_id == "example-gitlab"
    assert diagnostic.source_path == "workflows/example-gitlab/definition.yml"


@pytest.mark.anyio
async def test_workflow_push_requires_publish_then_preserves_saved_version(
    session: AsyncSession, svc_role: Role
) -> None:
    workspace = await session.scalar(
        select(Workspace).where(Workspace.id == svc_role.workspace_id)
    )
    assert workspace is not None
    workspace.settings = {
        **(workspace.settings or {}),
        "git_repo_url": "git+ssh://git@github.com/example/synthetic-sync.git",
    }
    workflow = await _workflow(
        session, svc_role, "Synthetic published workflow", alias="synthetic-published"
    )
    await session.commit()
    vcs = FakeVcsServer()
    sync = WorkspaceSyncService(
        session=session, role=svc_role, transport_factory=vcs.transport_factory
    )
    params = WorkflowDslPublish(branch="sync/example", create_pr=False)
    with patch(
        "tracecat.workflow.store.service.WorkspaceSyncService.for_workspace",
        AsyncMock(return_value=sync),
    ):
        with patch.object(
            FakeVcsTransport, "write_files", new_callable=AsyncMock
        ) as writes:
            response = await publish_workflow(
                role=svc_role,
                session=session,
                workflow_id=WorkflowUUID.new(workflow.id),
                params=params,
            )
            writes.assert_not_awaited()
        assert isinstance(response, JSONResponse)
        assert response.status_code == 400
        error = WorkspaceSyncExportError.model_validate_json(bytes(response.body))
        assert error.diagnostics[0].code == "publish_required"
        assert workflow.version is None
        assert workflow.git_sync_branch is None
        assert (
            await session.scalar(select(func.count()).select_from(WorkflowDefinition))
            == 0
        )
        assert (
            await session.scalar(
                select(func.count()).select_from(WorkspaceSyncResourceMapping)
            )
            == 0
        )

        # Use the existing version-creation service, then edit the draft.
        dsl = await sync._get_workflow_dsl(workflow)
        saved = await WorkflowDefinitionsService(
            session=session, role=svc_role
        ).create_workflow_definition(WorkflowUUID.new(workflow.id), dsl)
        workflow.version = saved.version
        workflow.title = "Newer unpublished title"
        await session.commit()
        result = await publish_workflow(
            role=svc_role,
            session=session,
            workflow_id=WorkflowUUID.new(workflow.id),
            params=params,
        )
        assert isinstance(result, WorkflowDslPublishResult)
        assert result.status == "committed"
        projection = await sync.project_workspace(
            workflow_ids=[WorkflowUUID.new(workflow.id)], create_missing_mappings=False
        )
        definitions = [
            yaml.safe_load(content)
            for path, content in projection.files.items()
            if path.endswith("definition.yml")
        ]
        assert definitions[0]["definition"]["title"] == "Synthetic published workflow"
        assert workflow.version == 1


@pytest.mark.anyio
async def test_invalid_required_child_stops_preview_and_apply_without_writes(
    session: AsyncSession, svc_role: Role
) -> None:
    workspace = await session.scalar(
        select(Workspace).where(Workspace.id == svc_role.workspace_id)
    )
    assert workspace is not None
    workspace.settings = {
        **(workspace.settings or {}),
        "git_repo_url": "git+ssh://git@github.com/example/synthetic-sync.git",
    }
    child = await _workflow(
        session, svc_role, "Synthetic child", alias="synthetic-child", empty=True
    )
    parent = await _workflow(
        session,
        svc_role,
        "Synthetic parent",
        alias="synthetic-parent",
        child="synthetic-child",
    )
    await session.commit()
    transport = AsyncMock()
    sync = WorkspaceSyncService(
        session=session,
        role=svc_role,
        transport_factory=lambda *_args, **_kwargs: transport,
    )
    selection = [
        ResourceRef(resource_type=SyncResourceType.WORKFLOW, local_id=parent.id)
    ]
    errors = []
    for operation in [
        sync.preview_export_workspace(
            WorkspaceSyncExportPreviewRequest(resources=selection)
        ),
        sync.export_workspace(
            WorkspaceSyncExportRequest(
                resources=selection, message="Synthetic sync", branch="sync/example"
            )
        ),
    ]:
        with pytest.raises(WorkflowExportValidationError) as raised:
            await operation
        errors.append(raised.value.diagnostics)
        assert len(raised.value.diagnostics) == 1
        diagnostic = raised.value.diagnostics[0]
        assert diagnostic.workflow_id == child.id
        assert diagnostic.workflow_title == "Synthetic child"
        assert diagnostic.code == "no_actions"
        assert diagnostic.source_id is None
        assert diagnostic.source_path is None
        assert (
            await session.scalar(
                select(func.count()).select_from(WorkspaceSyncResourceMapping)
            )
            == 0
        )
        transport.write_files.assert_not_awaited()
        assert parent.git_sync_branch is None
        assert child.version is None
    assert errors[0] == errors[1]


@pytest.mark.anyio
async def test_dependency_validation_names_each_workflow_before_mapping_writes(
    session: AsyncSession, svc_role: Role
) -> None:
    workspace = await session.scalar(
        select(Workspace).where(Workspace.id == svc_role.workspace_id)
    )
    assert workspace is not None
    workspace.settings = {
        **(workspace.settings or {}),
        "git_repo_url": "git+ssh://git@github.com/example/synthetic-sync.git",
    }
    workflows = [
        await _workflow(
            session, svc_role, "Synthetic duplicate title", alias=f"synthetic-{index}"
        )
        for index in range(2)
    ]
    for workflow in workflows:
        action = await session.scalar(
            select(Action).where(Action.workflow_id == workflow.id)
        )
        assert action is not None
        action.type = "ai.preset_agent"
        action.inputs = "preset: synthetic-missing-preset"
    session.add(
        WorkspaceSyncResourceMapping(
            workspace_id=svc_role.workspace_id,
            provider="github",
            resource_type="workflow",
            local_id=workflows[0].id,
            source_id="synthetic-existing",
            source_path="workflows/synthetic-existing/definition.yml",
        )
    )
    await session.commit()
    transport = AsyncMock()
    sync = WorkspaceSyncService(
        session=session,
        role=svc_role,
        transport_factory=lambda *_args, **_kwargs: transport,
    )
    selection = [
        ResourceRef(resource_type=SyncResourceType.WORKFLOW, local_id=workflow.id)
        for workflow in workflows
    ]
    errors = []
    for operation in [
        sync.preview_export_workspace(
            WorkspaceSyncExportPreviewRequest(resources=selection)
        ),
        sync.export_workspace(
            WorkspaceSyncExportRequest(
                resources=selection, message="Synthetic sync", branch="sync/example"
            )
        ),
    ]:
        with pytest.raises(WorkflowExportValidationError) as raised:
            await operation
        diagnostics = {item.workflow_id: item for item in raised.value.diagnostics}
        assert set(diagnostics) == {workflow.id for workflow in workflows}
        assert all(item.stage == "dependency" for item in diagnostics.values())
        assert diagnostics[workflows[0].id].source_id == "synthetic-existing"
        assert (
            diagnostics[workflows[0].id].source_path
            == "workflows/synthetic-existing/definition.yml"
        )
        assert diagnostics[workflows[1].id].source_id is None
        assert diagnostics[workflows[1].id].source_path is None
        assert "synthetic-missing-preset" not in str(raised.value)
        assert (
            await session.scalar(
                select(func.count()).select_from(WorkspaceSyncResourceMapping)
            )
            == 1
        )
        transport.write_files.assert_not_awaited()
        errors.append(raised.value.diagnostics)
    assert errors[0] == errors[1]
