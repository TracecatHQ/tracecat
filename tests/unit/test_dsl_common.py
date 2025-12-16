"""Tests for tracecat.dsl.common module."""

from __future__ import annotations

import uuid

from tracecat.dsl.view import (
    RFEdge,
    RFGraph,
    TriggerNode,
    TriggerNodeData,
    UDFNode,
    UDFNodeData,
)


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
