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
