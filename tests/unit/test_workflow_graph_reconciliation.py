"""Tests for workflow graph reconciliation logic.

Ensures stale upstream edge references are removed when actions are deleted
or when trigger references become invalid.
"""

import uuid
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.db.models import Action, Workflow, Workspace
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.workflow.management.management import WorkflowsManagementService

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def workflow_with_actions(
    session: AsyncSession,
    svc_workspace: Workspace,
) -> AsyncGenerator[tuple[Workflow, list[Action]], None]:
    """Create a workflow with actions and upstream_edges graph data."""
    workflow_id = uuid.uuid4()

    workflow = Workflow(
        id=workflow_id,
        title="Test Workflow",
        description="Test workflow for reconciliation",
        status="offline",
        workspace_id=svc_workspace.id,
        config={},
    )
    session.add(workflow)
    await session.flush()

    trigger_edge = {"source_id": f"trigger-{workflow_id}", "source_type": "trigger"}

    action1 = Action(
        id=uuid.uuid4(),
        workspace_id=svc_workspace.id,
        workflow_id=workflow_id,
        type="core.http_request",
        title="Action 1",
        description="First action",
        inputs="",
        control_flow={},
        position_x=100,
        position_y=100,
        upstream_edges=[trigger_edge],
    )

    action2 = Action(
        id=uuid.uuid4(),
        workspace_id=svc_workspace.id,
        workflow_id=workflow_id,
        type="core.http_request",
        title="Action 2",
        description="Second action",
        inputs="",
        control_flow={},
        position_x=200,
        position_y=200,
        upstream_edges=[
            {
                "source_id": str(action1.id),
                "source_type": "udf",
                "source_handle": "success",
            }
        ],
    )

    session.add_all([action1, action2])
    await session.commit()
    await session.refresh(workflow, ["actions"])

    try:
        yield workflow, [action1, action2]
    finally:
        # Cleanup - refresh to get current state, some actions may have been deleted
        await session.refresh(workflow, ["actions"])
        for action in workflow.actions:
            await session.delete(action)
        await session.delete(workflow)
        await session.commit()


@pytest.mark.anyio
async def test_reconcile_no_changes_needed(
    session: AsyncSession,
    svc_role: Role,
    workflow_with_actions: tuple[Workflow, list[Action]],
) -> None:
    """Return False when upstream_edges are already in sync."""
    workflow, _ = workflow_with_actions

    service = WorkflowsManagementService(session, role=svc_role)
    result = await service._reconcile_graph_object_with_actions(workflow)

    assert result is False
    await session.refresh(workflow, ["actions"])
    assert len(workflow.actions) == 2
    assert workflow.actions[0].upstream_edges
    assert workflow.actions[1].upstream_edges


@pytest.mark.anyio
async def test_reconcile_removes_stale_nodes(
    session: AsyncSession,
    svc_role: Role,
    workflow_with_actions: tuple[Workflow, list[Action]],
) -> None:
    """Remove upstream_edges that point to deleted actions."""
    workflow, actions = workflow_with_actions

    deleted_action = actions[0]
    await session.delete(deleted_action)
    await session.commit()
    await session.refresh(workflow, ["actions"])

    service = WorkflowsManagementService(session, role=svc_role)
    result = await service._reconcile_graph_object_with_actions(workflow)

    assert result is True
    await session.refresh(workflow, ["actions"])
    remaining_action = workflow.actions[0]
    assert remaining_action.upstream_edges == []


@pytest.mark.anyio
async def test_reconcile_removes_stale_edges(
    session: AsyncSession,
    svc_role: Role,
    workflow_with_actions: tuple[Workflow, list[Action]],
) -> None:
    """Remove trigger edges with invalid IDs."""
    workflow, actions = workflow_with_actions
    action1_id = actions[0].id

    # Corrupt trigger edge
    actions[0].upstream_edges = [
        {"source_id": "trigger-invalid", "source_type": "trigger"}
    ]
    session.add(actions[0])
    await session.commit()
    await session.refresh(workflow, ["actions"])

    service = WorkflowsManagementService(session, role=svc_role)
    result = await service._reconcile_graph_object_with_actions(workflow)

    assert result is True
    await session.refresh(workflow, ["actions"])
    # Find action1 by ID since workflow.actions order is not guaranteed
    action1 = next(a for a in workflow.actions if a.id == action1_id)
    assert action1.upstream_edges == []


@pytest.mark.anyio
async def test_reconcile_removes_all_stale_references(
    session: AsyncSession,
    svc_role: Role,
    workflow_with_actions: tuple[Workflow, list[Action]],
) -> None:
    """Handle workflows without actions gracefully."""
    workflow, actions = workflow_with_actions

    for action in actions:
        await session.delete(action)
    await session.commit()
    await session.refresh(workflow, ["actions"])

    service = WorkflowsManagementService(session, role=svc_role)
    result = await service._reconcile_graph_object_with_actions(workflow)

    assert result is False


@pytest.mark.anyio
async def test_reconcile_preserves_trigger_node(
    session: AsyncSession,
    svc_role: Role,
    workflow_with_actions: tuple[Workflow, list[Action]],
) -> None:
    """Trigger edges with correct IDs should be preserved."""
    workflow, actions = workflow_with_actions

    service = WorkflowsManagementService(session, role=svc_role)
    await service._reconcile_graph_object_with_actions(workflow)

    await session.refresh(workflow, ["actions"])
    trigger_edges = [
        edge
        for edge in actions[0].upstream_edges
        if edge.get("source_type") == "trigger"
    ]
    assert trigger_edges


@pytest.mark.anyio
async def test_reconcile_persists_changes(
    session: AsyncSession,
    svc_role: Role,
    workflow_with_actions: tuple[Workflow, list[Action]],
) -> None:
    """Persist upstream_edges cleanup to the database."""
    workflow, actions = workflow_with_actions

    await session.delete(actions[0])
    await session.commit()
    await session.refresh(workflow, ["actions"])

    service = WorkflowsManagementService(session, role=svc_role)
    await service._reconcile_graph_object_with_actions(workflow)

    session.expire(workflow)
    await session.refresh(workflow, ["actions"])

    assert workflow.actions[0].upstream_edges == []


@pytest.mark.anyio
async def test_reconcile_empty_graph_object(
    session: AsyncSession,
    svc_workspace: Workspace,
    svc_role: Role,
) -> None:
    """Workflows without actions should not error."""
    workflow = Workflow(
        id=uuid.uuid4(),
        title="Empty Workflow",
        description="Workflow with no graph",
        status="offline",
        workspace_id=svc_workspace.id,
        config={},
    )
    session.add(workflow)
    await session.commit()
    await session.refresh(workflow, ["actions"])

    try:
        service = WorkflowsManagementService(session, role=svc_role)
        result = await service._reconcile_graph_object_with_actions(workflow)

        assert result is False
    finally:
        await session.delete(workflow)
        await session.commit()


@pytest.mark.anyio
async def test_get_workflow_triggers_reconciliation(
    session: AsyncSession,
    svc_role: Role,
    workflow_with_actions: tuple[Workflow, list[Action]],
) -> None:
    """get_workflow should reconcile stale upstream_edges before returning."""
    workflow, actions = workflow_with_actions
    workflow_id = WorkflowUUID.new(workflow.id)

    await session.delete(actions[0])
    await session.commit()

    service = WorkflowsManagementService(session, role=svc_role)
    session.expire_all()

    fetched_workflow = await service.get_workflow(workflow_id)

    assert fetched_workflow is not None
    assert len(fetched_workflow.actions) == 1
    assert fetched_workflow.actions[0].upstream_edges == []


@pytest.mark.anyio
async def test_reconcile_preserves_viewport(
    session: AsyncSession,
    svc_role: Role,
    workflow_with_actions: tuple[Workflow, list[Action]],
) -> None:
    """Non-graph action fields should be preserved during reconciliation."""
    workflow, actions = workflow_with_actions
    action2_id = actions[1].id

    actions[1].description = "Updated description"
    session.add(actions[1])
    await session.commit()
    await session.refresh(workflow, ["actions"])

    service = WorkflowsManagementService(session, role=svc_role)
    await service._reconcile_graph_object_with_actions(workflow)

    await session.refresh(workflow, ["actions"])
    # Find action2 by ID since workflow.actions order is not guaranteed
    action2 = next(a for a in workflow.actions if a.id == action2_id)
    assert action2.description == "Updated description"
