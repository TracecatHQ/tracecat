import uuid
from typing import Literal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.cases.durations.schemas import CaseDurationCreate, CaseDurationUpdate
from tracecat.cases.durations.service import CaseDurationService
from tracecat.cases.enums import CaseEventType, CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.schemas import (
    CaseCreate,
    CaseTaskCreate,
    CaseTaskUpdate,
    CaseUpdate,
)
from tracecat.cases.service import CasesService, CaseTasksService
from tracecat.db.models import (
    Case,
    CaseDurationDefinition,
    CaseEvent,
    Workflow,
    Workspace,
)
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.identifiers import WorkflowUUID
from tracecat.service import BaseWorkspaceService

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def allow_cases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(BaseWorkspaceService, "require_entitlement", AsyncMock())


@pytest.fixture
async def reference_case(session: AsyncSession, svc_role: Role) -> Case:
    case = Case(
        workspace_id=svc_role.workspace_id,
        summary="Synthetic reference case",
        description="Synthetic reference validation",
        status=CaseStatus.NEW,
        priority=CasePriority.UNKNOWN,
        severity=CaseSeverity.UNKNOWN,
    )
    session.add(case)
    await session.commit()
    return case


@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("reference", ["missing", "other_workspace", "same_workspace"])
async def test_task_workflow_reference_without_defaults(
    session: AsyncSession,
    svc_role: Role,
    reference_case: Case,
    operation: Literal["create", "update"],
    reference: Literal["missing", "other_workspace", "same_workspace"],
) -> None:
    workflow_id = uuid.uuid4()
    if reference != "missing":
        workspace_id = svc_role.workspace_id
        if reference == "other_workspace":
            workspace = Workspace(
                name="Synthetic other workspace",
                organization_id=svc_role.organization_id,
            )
            session.add(workspace)
            await session.flush()
            workspace_id = workspace.id
        workflow = Workflow(
            workspace_id=workspace_id, title="Synthetic workflow", description=""
        )
        session.add(workflow)
        await session.commit()
        workflow_id = workflow.id

    service = CaseTasksService(session, role=svc_role)
    task = None
    if operation == "update":
        task = await service.create_task(
            reference_case.id, CaseTaskCreate(title="Synthetic task")
        )

    async def write() -> None:
        if operation == "create":
            created = await service.create_task(
                reference_case.id,
                CaseTaskCreate(
                    title="Synthetic task", workflow_id=WorkflowUUID.new(workflow_id)
                ),
            )
            assert created.workflow_id == workflow_id
        else:
            assert task is not None
            updated = await service.update_task(
                task.id, CaseTaskUpdate(workflow_id=WorkflowUUID.new(workflow_id))
            )
            assert updated.workflow_id == workflow_id

    if reference == "same_workspace":
        await write()
    else:
        with pytest.raises(TracecatNotFoundError, match="Workflow not found"):
            await write()
        if task is not None:
            await session.refresh(task)
            assert task.workflow_id is None


@pytest.mark.parametrize("resource", ["case", "task"])
@pytest.mark.parametrize("operation", ["create", "update"])
async def test_missing_assignee_is_validation_error(
    session: AsyncSession,
    svc_role: Role,
    reference_case: Case,
    resource: Literal["case", "task"],
    operation: Literal["create", "update"],
) -> None:
    assignee_id = uuid.uuid4()
    cases = CasesService(session, role=svc_role)
    tasks = CaseTasksService(session, role=svc_role)
    task = None
    if resource == "task" and operation == "update":
        task = await tasks.create_task(
            reference_case.id, CaseTaskCreate(title="Synthetic task")
        )

    with pytest.raises(TracecatValidationError, match="Assignee not found"):
        if resource == "case" and operation == "create":
            await cases.create_case(
                CaseCreate(
                    summary="Synthetic case",
                    description="",
                    assignee_id=assignee_id,
                    status=CaseStatus.NEW,
                    priority=CasePriority.UNKNOWN,
                    severity=CaseSeverity.UNKNOWN,
                )
            )
        elif resource == "case":
            await cases.update_case(reference_case, CaseUpdate(assignee_id=assignee_id))
        elif operation == "create":
            await tasks.create_task(
                reference_case.id,
                CaseTaskCreate(title="Synthetic task", assignee_id=assignee_id),
            )
        else:
            assert task is not None
            await tasks.update_task(task.id, CaseTaskUpdate(assignee_id=assignee_id))


@pytest.mark.parametrize("field", ["summary", "description"])
async def test_case_required_text_cannot_be_cleared(
    session: AsyncSession, svc_role: Role, reference_case: Case, field: str
) -> None:
    params = CaseUpdate.model_validate({field: None})
    with pytest.raises(TracecatValidationError, match="cannot be null"):
        await CasesService(session, role=svc_role).update_case(reference_case, params)


@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("field", ["start_event_id", "end_event_id"])
@pytest.mark.parametrize(
    "reference", ["missing", "other_case", "other_workspace", "same_case"]
)
async def test_duration_event_references_are_case_scoped(
    session: AsyncSession,
    svc_role: Role,
    reference_case: Case,
    operation: Literal["create", "update"],
    field: str,
    reference: Literal["missing", "other_case", "other_workspace", "same_case"],
) -> None:
    definition = CaseDurationDefinition(
        workspace_id=svc_role.workspace_id,
        name="Synthetic duration",
        start_event_type=CaseEventType.CASE_CREATED,
        end_event_type=CaseEventType.CASE_CLOSED,
    )
    session.add(definition)
    event_id = uuid.uuid4()
    if reference != "missing":
        event_case = reference_case
        if reference in {"other_case", "other_workspace"}:
            workspace_id = svc_role.workspace_id
            if reference == "other_workspace":
                workspace = Workspace(
                    name="Synthetic event workspace",
                    organization_id=svc_role.organization_id,
                )
                session.add(workspace)
                await session.flush()
                workspace_id = workspace.id
            event_case = Case(
                workspace_id=workspace_id,
                summary="Synthetic event case",
                description="",
                status=CaseStatus.NEW,
                priority=CasePriority.UNKNOWN,
                severity=CaseSeverity.UNKNOWN,
            )
            session.add(event_case)
            await session.flush()
        event = CaseEvent(
            workspace_id=event_case.workspace_id,
            case_id=event_case.id,
            type=CaseEventType.CASE_CREATED,
            data={},
        )
        session.add(event)
        await session.flush()
        event_id = event.id
    await session.commit()

    service = CaseDurationService(session, role=svc_role)
    duration = None
    if operation == "update":
        duration = await service.create_duration(
            reference_case, CaseDurationCreate(definition_id=definition.id)
        )

    async def write() -> None:
        if operation == "create":
            params = CaseDurationCreate.model_validate(
                {"definition_id": definition.id, field: event_id}
            )
            created = await service.create_duration(reference_case, params)
            assert (
                created.start_event_id
                if field == "start_event_id"
                else created.end_event_id
            ) == event_id
        else:
            assert duration is not None
            updated = await service.update_duration(
                reference_case,
                duration.id,
                CaseDurationUpdate.model_validate({field: event_id}),
            )
            assert (
                updated.start_event_id
                if field == "start_event_id"
                else updated.end_event_id
            ) == event_id

    if reference == "same_case":
        await write()
    else:
        with pytest.raises(
            TracecatNotFoundError, match="Duration event not found on case"
        ):
            await write()
