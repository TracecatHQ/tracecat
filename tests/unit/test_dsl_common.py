"""Tests for tracecat.dsl.common module."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from tracecat.db.models import Action
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


def _make_trigger_node(trigger_id: str | None = None) -> TriggerNode:
    """Create a trigger node for the graph."""
    if trigger_id is None:
        trigger_id = f"trigger-{uuid.uuid4()}"
    return TriggerNode(
        id=trigger_id,
        data=TriggerNodeData(title="Trigger"),
    )


def _make_udf_node(
    node_id: str | uuid.UUID, action_type: str = "core.http_request"
) -> UDFNode:
    """Create a UDF node for the graph.

    Note: UDFNode.id is typed as ActionID (uuid.UUID), so the node_id must be
    a valid UUID or UUID string. Legacy format IDs (act-<hex>) cannot be used
    directly with UDFNode.
    """
    return UDFNode(
        id=node_id if isinstance(node_id, uuid.UUID) else uuid.UUID(node_id),
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

    def test_build_with_multiple_actions_and_dependencies(self) -> None:
        """Test building action statements with multiple actions and dependencies."""
        # Create actions with UUIDs
        action1_uuid = uuid.uuid4()
        action2_uuid = uuid.uuid4()
        action3_uuid = uuid.uuid4()

        action1 = _make_mock_action(action1_uuid, "First Action")
        action2 = _make_mock_action(action2_uuid, "Second Action")
        action3 = _make_mock_action(action3_uuid, "Third Action")
        actions = [action1, action2, action3]

        trigger = _make_trigger_node()
        node1 = _make_udf_node(action1_uuid)
        node2 = _make_udf_node(action2_uuid)
        node3 = _make_udf_node(action3_uuid)

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

        # Verify dependencies are correctly set
        second_stmt = next(s for s in statements if s.ref == "second_action")
        assert second_stmt.depends_on == ["first_action"]

        third_stmt = next(s for s in statements if s.ref == "third_action")
        assert third_stmt.depends_on == ["second_action"]

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
    """Tests for RFGraph.normalize_action_ids method.

    Note: Since UDFNode.id is typed as ActionID (uuid.UUID), node IDs are always
    valid UUIDs after construction. The normalize_action_ids method is a no-op
    for graphs with valid UUID node IDs - it exists for potential backward
    compatibility scenarios where raw JSON graphs might have non-standard IDs.
    """

    def test_normalize_preserves_uuid_ids(self) -> None:
        """Test that UUID IDs are preserved unchanged."""
        action_uuid = uuid.uuid4()

        trigger = _make_trigger_node()
        node = _make_udf_node(action_uuid)

        graph = RFGraph(
            nodes=[trigger, node],
            edges=[RFEdge(source=trigger.id, target=node.id)],
        )

        normalized_graph = graph.normalize_action_ids()

        # Should be unchanged
        action_nodes = normalized_graph.action_nodes()
        assert len(action_nodes) == 1
        assert action_nodes[0].id == action_uuid

    def test_normalize_preserves_trigger_node(self) -> None:
        """Test that trigger nodes are not affected by normalization."""
        action_uuid = uuid.uuid4()
        trigger_id = f"trigger-{uuid.uuid4()}"

        trigger = _make_trigger_node(trigger_id)
        node = _make_udf_node(action_uuid)

        graph = RFGraph(
            nodes=[trigger, node],
            edges=[RFEdge(source=trigger_id, target=node.id)],
        )

        normalized_graph = graph.normalize_action_ids()

        # Trigger node should be unchanged
        assert normalized_graph.trigger.id == trigger_id

    def test_normalize_returns_same_graph_when_no_changes_needed(self) -> None:
        """Test that normalize returns self when no normalization is needed."""
        action_uuid = uuid.uuid4()

        trigger = _make_trigger_node()
        node = _make_udf_node(action_uuid)

        graph = RFGraph(
            nodes=[trigger, node],
            edges=[RFEdge(source=trigger.id, target=node.id)],
        )

        normalized_graph = graph.normalize_action_ids()

        # Should return the same instance when no changes needed
        assert normalized_graph is graph

    def test_normalize_with_multiple_nodes(self) -> None:
        """Test normalize_action_ids with multiple action nodes."""
        action1_uuid = uuid.uuid4()
        action2_uuid = uuid.uuid4()

        trigger = _make_trigger_node()
        node1 = _make_udf_node(action1_uuid)
        node2 = _make_udf_node(action2_uuid)

        graph = RFGraph(
            nodes=[trigger, node1, node2],
            edges=[
                RFEdge(source=trigger.id, target=node1.id),
                RFEdge(source=node1.id, target=node2.id),
            ],
        )

        normalized_graph = graph.normalize_action_ids()

        # All nodes should be preserved
        action_nodes = normalized_graph.action_nodes()
        assert len(action_nodes) == 2
        node_ids = {n.id for n in action_nodes}
        assert node_ids == {action1_uuid, action2_uuid}
