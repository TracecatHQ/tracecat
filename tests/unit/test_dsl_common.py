"""Tests for tracecat.dsl.common module."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from unittest.mock import MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.db.models import Action, Workflow, Workspace
from tracecat.dsl.common import _normalize_action_id, build_action_statements
from tracecat.dsl.view import (
    RFEdge,
    RFGraph,
    TriggerNode,
    TriggerNodeData,
    UDFNode,
    UDFNodeData,
)
from tracecat.exceptions import TracecatValidationError
from tracecat.identifiers.action import ActionUUID
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.workflow.management.management import WorkflowsManagementService
from tracecat.workflow.management.schemas import WorkflowUpdate


def _make_mock_action(
    action_id: uuid.UUID,
    title: str,
    action_type: str = "core.http_request",
) -> Action:
    """Create a mock Action object with the required attributes."""
    action = MagicMock()
    action.id = action_id
    action.title = title
    action.type = action_type
    action.inputs = ""  # Empty YAML string
    action.control_flow = {}
    action.is_interactive = False
    action.interaction = None
    # Simulate the ref property (slugified title)
    action.ref = title.lower().replace(" ", "_")
    return action


def _make_trigger_node(trigger_id: str = "trigger-test") -> TriggerNode:
    """Create a trigger node for the graph."""
    return TriggerNode(
        id=trigger_id,
        data=TriggerNodeData(title="Trigger"),
    )


def _make_udf_node(node_id: str, action_type: str = "core.http_request") -> UDFNode:
    """Create a UDF node for the graph."""
    return UDFNode(
        id=node_id,
        data=UDFNodeData(type=action_type),
    )


class TestNormalizeActionId:
    """Tests for _normalize_action_id helper function."""

    def test_normalize_uuid_string(self) -> None:
        """Test normalizing a standard UUID string."""
        uuid_str = "550e8400-e29b-41d4-a716-446655440000"
        result = _normalize_action_id(uuid_str)
        assert isinstance(result, ActionUUID)
        assert str(result) == uuid_str

    def test_normalize_legacy_prefixed_id(self) -> None:
        """Test normalizing a legacy prefixed ID (act-<32hex>)."""
        # Create a UUID and its legacy representation
        test_uuid = uuid.UUID("550e8400-e29b-41d4-a716-446655440000")
        legacy_id = f"act-{test_uuid.hex}"  # act-550e8400e29b41d4a716446655440000

        result = _normalize_action_id(legacy_id)
        assert isinstance(result, ActionUUID)
        assert result == test_uuid

    def test_normalize_short_id(self) -> None:
        """Test normalizing a short ID (act_xxx)."""
        # Create a UUID and get its short representation
        test_uuid = ActionUUID.new_uuid4()
        short_id = test_uuid.short()

        result = _normalize_action_id(short_id)
        assert isinstance(result, ActionUUID)
        assert result == test_uuid

    def test_normalize_invalid_id_raises_validation_error(self) -> None:
        """Test that invalid IDs raise TracecatValidationError."""
        with pytest.raises(TracecatValidationError) as exc_info:
            _normalize_action_id("invalid-id-format")
        assert "Invalid action ID in workflow graph" in str(exc_info.value)

    def test_normalize_empty_string_raises_validation_error(self) -> None:
        """Test that empty strings raise TracecatValidationError."""
        with pytest.raises(TracecatValidationError) as exc_info:
            _normalize_action_id("")
        assert "Invalid action ID in workflow graph" in str(exc_info.value)


class TestBuildActionStatements:
    """Tests for build_action_statements function."""

    def test_build_with_uuid_string_node_ids(self) -> None:
        """Test building action statements with standard UUID string node IDs."""
        # Create actions with UUIDs
        action1_uuid = uuid.uuid4()
        action2_uuid = uuid.uuid4()

        action1 = _make_mock_action(action1_uuid, "First Action")
        action2 = _make_mock_action(action2_uuid, "Second Action")
        actions = [action1, action2]

        # Create graph with UUID string node IDs
        trigger = _make_trigger_node()
        node1 = _make_udf_node(str(action1_uuid))
        node2 = _make_udf_node(str(action2_uuid))

        graph = RFGraph(
            nodes=[trigger, node1, node2],
            edges=[
                RFEdge(source=trigger.id, target=node1.id),
                RFEdge(source=node1.id, target=node2.id),
            ],
        )

        statements = build_action_statements(graph, actions)

        assert len(statements) == 2
        refs = {stmt.ref for stmt in statements}
        assert refs == {"first_action", "second_action"}

        # Verify dependency is correctly set
        second_stmt = next(s for s in statements if s.ref == "second_action")
        assert second_stmt.depends_on == ["first_action"]

    def test_build_with_legacy_prefixed_node_ids(self) -> None:
        """Test building action statements with legacy prefixed node IDs (act-<32hex>)."""
        # Create actions with UUIDs
        action1_uuid = uuid.uuid4()
        action2_uuid = uuid.uuid4()

        action1 = _make_mock_action(action1_uuid, "First Action")
        action2 = _make_mock_action(action2_uuid, "Second Action")
        actions = [action1, action2]

        # Create graph with LEGACY prefixed node IDs
        legacy_id1 = f"act-{action1_uuid.hex}"
        legacy_id2 = f"act-{action2_uuid.hex}"

        trigger = _make_trigger_node()
        node1 = _make_udf_node(legacy_id1)
        node2 = _make_udf_node(legacy_id2)

        graph = RFGraph(
            nodes=[trigger, node1, node2],
            edges=[
                RFEdge(source=trigger.id, target=node1.id),
                RFEdge(source=node1.id, target=node2.id),
            ],
        )

        # This should NOT raise KeyError - it should normalize the IDs
        statements = build_action_statements(graph, actions)

        assert len(statements) == 2
        refs = {stmt.ref for stmt in statements}
        assert refs == {"first_action", "second_action"}

        # Verify dependency is correctly set
        second_stmt = next(s for s in statements if s.ref == "second_action")
        assert second_stmt.depends_on == ["first_action"]

    def test_build_with_short_id_node_ids(self) -> None:
        """Test building action statements with short ID node IDs (act_xxx)."""
        # Create actions with UUIDs
        action1_uuid = uuid.uuid4()
        action2_uuid = uuid.uuid4()

        action1 = _make_mock_action(action1_uuid, "First Action")
        action2 = _make_mock_action(action2_uuid, "Second Action")
        actions = [action1, action2]

        # Create graph with short ID node IDs
        short_id1 = ActionUUID.from_uuid(action1_uuid).short()
        short_id2 = ActionUUID.from_uuid(action2_uuid).short()

        trigger = _make_trigger_node()
        node1 = _make_udf_node(short_id1)
        node2 = _make_udf_node(short_id2)

        graph = RFGraph(
            nodes=[trigger, node1, node2],
            edges=[
                RFEdge(source=trigger.id, target=node1.id),
                RFEdge(source=node1.id, target=node2.id),
            ],
        )

        statements = build_action_statements(graph, actions)

        assert len(statements) == 2
        refs = {stmt.ref for stmt in statements}
        assert refs == {"first_action", "second_action"}

    def test_build_with_mixed_id_formats(self) -> None:
        """Test building action statements with mixed ID formats in the graph."""
        # Create actions with UUIDs
        action1_uuid = uuid.uuid4()
        action2_uuid = uuid.uuid4()
        action3_uuid = uuid.uuid4()

        action1 = _make_mock_action(action1_uuid, "First Action")
        action2 = _make_mock_action(action2_uuid, "Second Action")
        action3 = _make_mock_action(action3_uuid, "Third Action")
        actions = [action1, action2, action3]

        # Create graph with MIXED ID formats
        uuid_str = str(action1_uuid)
        legacy_id = f"act-{action2_uuid.hex}"
        short_id = ActionUUID.from_uuid(action3_uuid).short()

        trigger = _make_trigger_node()
        node1 = _make_udf_node(uuid_str)
        node2 = _make_udf_node(legacy_id)
        node3 = _make_udf_node(short_id)

        graph = RFGraph(
            nodes=[trigger, node1, node2, node3],
            edges=[
                RFEdge(source=trigger.id, target=node1.id),
                RFEdge(source=node1.id, target=node2.id),
                RFEdge(source=node2.id, target=node3.id),
            ],
        )

        statements = build_action_statements(graph, actions)

        assert len(statements) == 3
        refs = {stmt.ref for stmt in statements}
        assert refs == {"first_action", "second_action", "third_action"}

    def test_build_with_invalid_node_id_raises_validation_error(self) -> None:
        """Test that invalid node IDs raise TracecatValidationError instead of KeyError."""
        action_uuid = uuid.uuid4()
        action = _make_mock_action(action_uuid, "Test Action")
        actions = [action]

        # Create graph with an INVALID node ID
        trigger = _make_trigger_node()
        node = _make_udf_node("invalid-node-id-format")

        graph = RFGraph(
            nodes=[trigger, node],
            edges=[RFEdge(source=trigger.id, target=node.id)],
        )

        with pytest.raises(TracecatValidationError) as exc_info:
            build_action_statements(graph, actions)
        assert "Invalid action ID in workflow graph" in str(exc_info.value)
        assert "invalid-node-id-format" in str(exc_info.value)

    def test_build_single_action_no_dependencies(self) -> None:
        """Test building action statements for a single action with no dependencies."""
        action_uuid = uuid.uuid4()
        action = _make_mock_action(action_uuid, "Single Action")
        actions = [action]

        trigger = _make_trigger_node()
        node = _make_udf_node(str(action_uuid))

        graph = RFGraph(
            nodes=[trigger, node],
            edges=[RFEdge(source=trigger.id, target=node.id)],
        )

        statements = build_action_statements(graph, actions)

        assert len(statements) == 1
        assert statements[0].ref == "single_action"
        assert statements[0].depends_on == []

    def test_build_empty_actions_list(self) -> None:
        """Test building action statements with empty actions list."""
        trigger = _make_trigger_node()

        graph = RFGraph(
            nodes=[trigger],
            edges=[],
        )

        statements = build_action_statements(graph, [])

        assert len(statements) == 0


class TestRFGraphNormalizeActionIds:
    """Tests for RFGraph.normalize_action_ids method."""

    def test_normalize_legacy_prefixed_ids(self) -> None:
        """Test normalizing legacy prefixed action IDs (act-<32hex>)."""
        action_uuid = uuid.uuid4()
        legacy_id = f"act-{action_uuid.hex}"

        trigger = _make_trigger_node()
        node = _make_udf_node(legacy_id)

        graph = RFGraph(
            nodes=[trigger, node],
            edges=[RFEdge(source=trigger.id, target=node.id)],
        )

        normalized_graph = graph.normalize_action_ids()

        # Check node ID is normalized to UUID string format
        action_nodes = normalized_graph.action_nodes()
        assert len(action_nodes) == 1
        assert action_nodes[0].id == str(action_uuid)

        # Check edge target is also normalized (trigger->node edge)
        assert len(normalized_graph.edges) == 1
        assert normalized_graph.edges[0].target == str(action_uuid)

    def test_normalize_legacy_ids_in_edges(self) -> None:
        """Test that edge source/target IDs are normalized."""
        action1_uuid = uuid.uuid4()
        action2_uuid = uuid.uuid4()
        legacy_id1 = f"act-{action1_uuid.hex}"
        legacy_id2 = f"act-{action2_uuid.hex}"

        trigger = _make_trigger_node()
        node1 = _make_udf_node(legacy_id1)
        node2 = _make_udf_node(legacy_id2)

        graph = RFGraph(
            nodes=[trigger, node1, node2],
            edges=[
                RFEdge(source=trigger.id, target=legacy_id1),
                RFEdge(source=legacy_id1, target=legacy_id2),
            ],
        )

        normalized_graph = graph.normalize_action_ids()

        # Check edges have normalized IDs
        action_edges = normalized_graph.action_edges()
        assert len(action_edges) == 1
        assert action_edges[0].source == str(action1_uuid)
        assert action_edges[0].target == str(action2_uuid)

    def test_normalize_preserves_uuid_string_ids(self) -> None:
        """Test that already-normalized UUID string IDs are unchanged."""
        action_uuid = uuid.uuid4()
        uuid_str = str(action_uuid)

        trigger = _make_trigger_node()
        node = _make_udf_node(uuid_str)

        graph = RFGraph(
            nodes=[trigger, node],
            edges=[RFEdge(source=trigger.id, target=uuid_str)],
        )

        normalized_graph = graph.normalize_action_ids()

        # Should be unchanged
        action_nodes = normalized_graph.action_nodes()
        assert len(action_nodes) == 1
        assert action_nodes[0].id == uuid_str

    def test_normalize_mixed_id_formats(self) -> None:
        """Test normalizing a graph with mixed ID formats."""
        action1_uuid = uuid.uuid4()
        action2_uuid = uuid.uuid4()
        action3_uuid = uuid.uuid4()

        # Mix of formats: UUID string, legacy prefixed, short ID
        uuid_str = str(action1_uuid)
        legacy_id = f"act-{action2_uuid.hex}"
        short_id = ActionUUID.from_uuid(action3_uuid).short()

        trigger = _make_trigger_node()
        node1 = _make_udf_node(uuid_str)
        node2 = _make_udf_node(legacy_id)
        node3 = _make_udf_node(short_id)

        graph = RFGraph(
            nodes=[trigger, node1, node2, node3],
            edges=[
                RFEdge(source=trigger.id, target=uuid_str),
                RFEdge(source=uuid_str, target=legacy_id),
                RFEdge(source=legacy_id, target=short_id),
            ],
        )

        normalized_graph = graph.normalize_action_ids()

        # All action nodes should have UUID string format
        action_nodes = normalized_graph.action_nodes()
        node_ids = {n.id for n in action_nodes}
        expected_ids = {str(action1_uuid), str(action2_uuid), str(action3_uuid)}
        assert node_ids == expected_ids

    def test_normalize_preserves_trigger_node(self) -> None:
        """Test that trigger nodes are not affected by normalization."""
        action_uuid = uuid.uuid4()
        legacy_id = f"act-{action_uuid.hex}"
        trigger_id = "trigger-test-workflow"

        trigger = _make_trigger_node(trigger_id)
        node = _make_udf_node(legacy_id)

        graph = RFGraph(
            nodes=[trigger, node],
            edges=[RFEdge(source=trigger_id, target=legacy_id)],
        )

        normalized_graph = graph.normalize_action_ids()

        # Trigger node should be unchanged
        assert normalized_graph.trigger.id == trigger_id

    def test_normalize_returns_same_graph_when_no_changes_needed(self) -> None:
        """Test that normalize returns self when no normalization is needed."""
        action_uuid = uuid.uuid4()
        uuid_str = str(action_uuid)

        trigger = _make_trigger_node()
        node = _make_udf_node(uuid_str)

        graph = RFGraph(
            nodes=[trigger, node],
            edges=[RFEdge(source=trigger.id, target=uuid_str)],
        )

        normalized_graph = graph.normalize_action_ids()

        # Should return the same instance when no changes needed
        assert normalized_graph is graph


# ============================================================================
# Integration tests for WorkflowsManagementService.update_workflow
# ============================================================================


@pytest.fixture
async def management_service(
    session: AsyncSession, svc_role: Role
) -> WorkflowsManagementService:
    """Create a workflow management service instance for testing."""
    return WorkflowsManagementService(session=session, role=svc_role)


@pytest.fixture
async def test_workflow(
    session: AsyncSession, svc_workspace: Workspace
) -> AsyncGenerator[Workflow, None]:
    """Create a test workflow with an initial graph object."""
    workflow = Workflow(
        title="test-workflow-normalization",
        workspace_id=svc_workspace.id,
        description="Test workflow for action ID normalization",
        status="offline",
        object={
            "nodes": [
                {
                    "id": "trigger-test",
                    "type": "trigger",
                    "position": {"x": 0, "y": 0},
                    "data": {"type": "trigger", "title": "Trigger"},
                }
            ],
            "edges": [],
            "viewport": {"x": 0, "y": 0, "zoom": 1},
        },
    )
    session.add(workflow)
    await session.commit()
    await session.refresh(workflow)
    try:
        yield workflow
    finally:
        await session.delete(workflow)
        await session.commit()


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_update_workflow_normalizes_legacy_action_ids(
    management_service: WorkflowsManagementService,
    test_workflow: Workflow,
) -> None:
    """Test that update_workflow normalizes legacy action IDs in the graph object.

    This integration test verifies that when a client sends a workflow update
    with legacy `act-<32hex>` format action IDs, they are normalized to canonical
    UUID string format before being saved to the database.
    """
    # Create UUIDs for our test actions
    action1_uuid = uuid.uuid4()
    action2_uuid = uuid.uuid4()

    # Create legacy format IDs (act-<32hex>)
    legacy_id1 = f"act-{action1_uuid.hex}"
    legacy_id2 = f"act-{action2_uuid.hex}"

    # Create graph object with legacy action IDs (simulating client input)
    graph_with_legacy_ids = {
        "nodes": [
            {
                "id": "trigger-test",
                "type": "trigger",
                "position": {"x": 0, "y": 0},
                "data": {"type": "trigger", "title": "Trigger"},
            },
            {
                "id": legacy_id1,
                "type": "udf",
                "position": {"x": 100, "y": 100},
                "data": {"type": "core.http_request", "isConfigured": True},
            },
            {
                "id": legacy_id2,
                "type": "udf",
                "position": {"x": 200, "y": 200},
                "data": {"type": "core.transform.reshape", "isConfigured": True},
            },
        ],
        "edges": [
            {"source": "trigger-test", "target": legacy_id1},
            {"source": legacy_id1, "target": legacy_id2},
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }

    # Update the workflow with the graph containing legacy IDs
    workflow_id = WorkflowUUID.new(test_workflow.id)
    params = WorkflowUpdate(object=graph_with_legacy_ids)

    updated_workflow = await management_service.update_workflow(workflow_id, params)

    # Verify the saved graph has normalized IDs
    saved_object = updated_workflow.object
    assert saved_object is not None

    # Extract action node IDs from saved graph
    action_node_ids = {
        node["id"] for node in saved_object["nodes"] if node["type"] == "udf"
    }

    # IDs should be canonical UUID strings, not legacy format
    expected_ids = {str(action1_uuid), str(action2_uuid)}
    assert action_node_ids == expected_ids

    # Verify edges also have normalized IDs
    action_edges = [
        edge for edge in saved_object["edges"] if edge["source"] != "trigger-test"
    ]
    assert len(action_edges) == 1
    assert action_edges[0]["source"] == str(action1_uuid)
    assert action_edges[0]["target"] == str(action2_uuid)

    # Verify trigger->action edge is also normalized
    trigger_edges = [
        edge for edge in saved_object["edges"] if edge["source"] == "trigger-test"
    ]
    assert len(trigger_edges) == 1
    assert trigger_edges[0]["target"] == str(action1_uuid)


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_update_workflow_preserves_already_normalized_ids(
    management_service: WorkflowsManagementService,
    test_workflow: Workflow,
) -> None:
    """Test that update_workflow doesn't modify already-normalized UUID string IDs."""
    action_uuid = uuid.uuid4()
    uuid_str = str(action_uuid)

    graph_with_uuid_ids = {
        "nodes": [
            {
                "id": "trigger-test",
                "type": "trigger",
                "position": {"x": 0, "y": 0},
                "data": {"type": "trigger", "title": "Trigger"},
            },
            {
                "id": uuid_str,
                "type": "udf",
                "position": {"x": 100, "y": 100},
                "data": {"type": "core.http_request", "isConfigured": True},
            },
        ],
        "edges": [{"source": "trigger-test", "target": uuid_str}],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }

    workflow_id = WorkflowUUID.new(test_workflow.id)
    params = WorkflowUpdate(object=graph_with_uuid_ids)

    updated_workflow = await management_service.update_workflow(workflow_id, params)

    # Verify the ID is unchanged
    saved_object = updated_workflow.object
    assert saved_object is not None
    action_node_ids = {
        node["id"] for node in saved_object["nodes"] if node["type"] == "udf"
    }
    assert action_node_ids == {uuid_str}
