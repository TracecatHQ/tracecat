"""Tests for workflow graph reconciliation logic.

Tests the _reconcile_graph_object_with_actions method which cleans up
stale node/edge references in workflow.object when Actions have been deleted.
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
    """Create a workflow with actions and a matching graph object."""
    workflow_id = uuid.uuid4()
    action1_id = uuid.uuid4()
    action2_id = uuid.uuid4()
    trigger_id = f"trigger-{workflow_id}"

    # Create workflow with graph object containing two action nodes
    workflow = Workflow(
        id=workflow_id,
        title="Test Workflow",
        description="Test workflow for reconciliation",
        status="offline",
        workspace_id=svc_workspace.id,
        object={
            "nodes": [
                {"id": trigger_id, "type": "trigger", "position": {"x": 0, "y": 0}},
                {
                    "id": str(action1_id),
                    "type": "udf",
                    "position": {"x": 100, "y": 100},
                },
                {
                    "id": str(action2_id),
                    "type": "udf",
                    "position": {"x": 200, "y": 200},
                },
            ],
            "edges": [
                {"source": trigger_id, "target": str(action1_id)},
                {"source": str(action1_id), "target": str(action2_id)},
            ],
            "viewport": {"x": 0, "y": 0, "zoom": 1},
        },
        config={},
    )
    session.add(workflow)
    await session.flush()

    # Create matching actions
    action1 = Action(
        id=action1_id,
        workspace_id=svc_workspace.id,
        workflow_id=workflow_id,
        type="core.http_request",
        title="Action 1",
        description="First action",
        inputs="",
        control_flow={},
    )
    action2 = Action(
        id=action2_id,
        workspace_id=svc_workspace.id,
        workflow_id=workflow_id,
        type="core.http_request",
        title="Action 2",
        description="Second action",
        inputs="",
        control_flow={},
    )
    session.add(action1)
    session.add(action2)
    await session.commit()

    # Refresh to load relationships
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
    """Test that reconciliation returns False when graph is already in sync."""
    workflow, actions = workflow_with_actions

    service = WorkflowsManagementService(session, role=svc_role)
    result = await service._reconcile_graph_object_with_actions(workflow)

    assert result is False
    # Graph should be unchanged - refresh to ensure we have latest state
    await session.refresh(workflow)
    assert workflow.object is not None
    assert len(workflow.object["nodes"]) == 3  # trigger + 2 actions
    assert len(workflow.object["edges"]) == 2


@pytest.mark.anyio
async def test_reconcile_removes_stale_nodes(
    session: AsyncSession,
    svc_role: Role,
    workflow_with_actions: tuple[Workflow, list[Action]],
) -> None:
    """Test that reconciliation removes nodes referencing deleted actions."""
    workflow, actions = workflow_with_actions

    # Delete one action from DB (simulating out-of-sync state)
    deleted_action = actions[1]
    await session.delete(deleted_action)
    await session.commit()
    await session.refresh(workflow, ["actions"])

    # Verify setup: graph has 3 nodes but only 1 action exists
    assert workflow.object is not None
    assert len(workflow.object["nodes"]) == 3
    assert len(workflow.actions) == 1

    service = WorkflowsManagementService(session, role=svc_role)
    result = await service._reconcile_graph_object_with_actions(workflow)

    assert result is True
    # Graph should now have only trigger + 1 valid action - refresh to ensure we have latest state
    await session.refresh(workflow)
    assert workflow.object is not None
    assert len(workflow.object["nodes"]) == 2
    node_ids = {node["id"] for node in workflow.object["nodes"]}
    assert str(deleted_action.id) not in node_ids
    assert str(actions[0].id) in node_ids


@pytest.mark.anyio
async def test_reconcile_removes_stale_edges(
    session: AsyncSession,
    svc_role: Role,
    workflow_with_actions: tuple[Workflow, list[Action]],
) -> None:
    """Test that reconciliation removes edges referencing deleted actions."""
    workflow, actions = workflow_with_actions

    # Delete one action from DB
    deleted_action = actions[1]
    deleted_action_id = str(deleted_action.id)
    await session.delete(deleted_action)
    await session.commit()
    await session.refresh(workflow, ["actions"])

    service = WorkflowsManagementService(session, role=svc_role)
    result = await service._reconcile_graph_object_with_actions(workflow)

    assert result is True
    # Edge from action1 -> action2 should be removed (action2 deleted) - refresh to ensure we have latest state
    await session.refresh(workflow)
    assert workflow.object is not None
    assert len(workflow.object["edges"]) == 1
    remaining_edge = workflow.object["edges"][0]
    assert remaining_edge["target"] != deleted_action_id
    assert remaining_edge["source"] != deleted_action_id


@pytest.mark.anyio
async def test_reconcile_removes_all_stale_references(
    session: AsyncSession,
    svc_role: Role,
    workflow_with_actions: tuple[Workflow, list[Action]],
) -> None:
    """Test that reconciliation removes all stale nodes and edges when all actions deleted."""
    workflow, actions = workflow_with_actions

    # Delete all actions
    for action in actions:
        await session.delete(action)
    await session.commit()
    await session.refresh(workflow, ["actions"])

    service = WorkflowsManagementService(session, role=svc_role)
    result = await service._reconcile_graph_object_with_actions(workflow)

    assert result is True
    # Only trigger node should remain - refresh to ensure we have latest state
    await session.refresh(workflow)
    assert workflow.object is not None
    assert len(workflow.object["nodes"]) == 1
    assert workflow.object["nodes"][0]["type"] == "trigger"
    # No edges should remain
    assert len(workflow.object["edges"]) == 0


@pytest.mark.anyio
async def test_reconcile_preserves_trigger_node(
    session: AsyncSession,
    svc_role: Role,
    workflow_with_actions: tuple[Workflow, list[Action]],
) -> None:
    """Test that reconciliation always preserves the trigger node."""
    workflow, actions = workflow_with_actions
    trigger_id = f"trigger-{workflow.id}"

    # Delete all actions
    for action in actions:
        await session.delete(action)
    await session.commit()
    await session.refresh(workflow, ["actions"])

    service = WorkflowsManagementService(session, role=svc_role)
    await service._reconcile_graph_object_with_actions(workflow)

    # Trigger should always be preserved - refresh to ensure we have latest state
    await session.refresh(workflow)
    assert workflow.object is not None
    assert len(workflow.object["nodes"]) == 1
    assert workflow.object["nodes"][0]["id"] == trigger_id
    assert workflow.object["nodes"][0]["type"] == "trigger"


@pytest.mark.anyio
async def test_reconcile_persists_changes(
    session: AsyncSession,
    svc_role: Role,
    workflow_with_actions: tuple[Workflow, list[Action]],
) -> None:
    """Test that reconciliation persists the cleaned graph to the database."""
    workflow, actions = workflow_with_actions

    # Delete one action
    await session.delete(actions[1])
    await session.commit()
    await session.refresh(workflow, ["actions"])

    service = WorkflowsManagementService(session, role=svc_role)
    await service._reconcile_graph_object_with_actions(workflow)

    # Fetch workflow fresh from DB to verify persistence
    session.expire(workflow)  # expire is sync
    await session.refresh(workflow)

    assert workflow.object is not None
    assert len(workflow.object["nodes"]) == 2  # trigger + 1 action


@pytest.mark.anyio
async def test_reconcile_empty_graph_object(
    session: AsyncSession,
    svc_workspace: Workspace,
    svc_role: Role,
) -> None:
    """Test that reconciliation handles None graph object gracefully."""
    workflow = Workflow(
        id=uuid.uuid4(),
        title="Empty Workflow",
        description="Workflow with no graph",
        status="offline",
        workspace_id=svc_workspace.id,
        object=None,
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
    """Test that get_workflow automatically reconciles stale references."""
    workflow, actions = workflow_with_actions
    workflow_id = WorkflowUUID.new(workflow.id)

    # Delete one action to create out-of-sync state
    await session.delete(actions[1])
    await session.commit()

    # Use a fresh session to simulate a new request
    service = WorkflowsManagementService(session, role=svc_role)

    # Clear session cache to force fresh load
    session.expire_all()

    fetched_workflow = await service.get_workflow(workflow_id)

    assert fetched_workflow is not None
    # Graph should be reconciled automatically
    assert fetched_workflow.object is not None
    assert len(fetched_workflow.object["nodes"]) == 2  # trigger + 1 remaining action
    assert len(fetched_workflow.object["edges"]) == 1  # only trigger -> action1


@pytest.mark.anyio
async def test_reconcile_preserves_viewport(
    session: AsyncSession,
    svc_role: Role,
    workflow_with_actions: tuple[Workflow, list[Action]],
) -> None:
    """Test that reconciliation preserves viewport and other graph properties."""
    workflow, actions = workflow_with_actions

    # Set custom viewport
    assert workflow.object is not None
    workflow.object["viewport"] = {"x": 100, "y": 200, "zoom": 1.5}
    workflow.object["customProperty"] = "should_be_preserved"
    session.add(workflow)
    await session.commit()

    # Delete one action
    await session.delete(actions[1])
    await session.commit()
    await session.refresh(workflow, ["actions"])

    service = WorkflowsManagementService(session, role=svc_role)
    await service._reconcile_graph_object_with_actions(workflow)

    # Viewport and other properties should be preserved - refresh to ensure we have latest state
    await session.refresh(workflow)
    assert workflow.object is not None
    assert workflow.object["viewport"] == {"x": 100, "y": 200, "zoom": 1.5}
    assert workflow.object["customProperty"] == "should_be_preserved"
