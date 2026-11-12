"""Tests for WorkflowActionService against a real database."""

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.db.models import Action, Workflow, Workspace
from tracecat.exceptions import TracecatNotFoundError
from tracecat.identifiers import WorkflowUUID
from tracecat.workflow.actions.schemas import ActionCreate
from tracecat.workflow.actions.service import WorkflowActionService

pytestmark = pytest.mark.usefixtures("db")


@pytest.mark.anyio
async def test_create_action_on_missing_workflow_raises_not_found(
    session: AsyncSession, svc_role: Role
) -> None:
    """A missing workflow is a 404, not an FK violation surfacing as a 500."""
    service = WorkflowActionService(session, role=svc_role)
    params = ActionCreate(
        workflow_id=WorkflowUUID.new(uuid.uuid4()),
        type="core.http_request",
        title="Orphan",
    )

    with pytest.raises(TracecatNotFoundError):
        await service.create_action(params)

    count = await session.scalar(
        select(func.count())
        .select_from(Action)
        .where(Action.workflow_id == params.workflow_id)
    )
    assert count == 0


@pytest.mark.anyio
async def test_create_action_on_existing_workflow(
    session: AsyncSession, svc_workspace: Workspace, svc_role: Role
) -> None:
    workflow = Workflow(
        id=uuid.uuid4(),
        title="Actions Workflow",
        description="Action create fixture",
        status="offline",
        workspace_id=svc_workspace.id,
        config={},
    )
    session.add(workflow)
    await session.commit()

    service = WorkflowActionService(session, role=svc_role)
    action = await service.create_action(
        ActionCreate(
            workflow_id=WorkflowUUID.new(workflow.id),
            type="core.http_request",
            title="Fetch",
        )
    )

    assert action.workflow_id == workflow.id
