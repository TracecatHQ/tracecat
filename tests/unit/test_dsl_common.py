"""Tests for tracecat.dsl.common module."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, cast

import pytest

from tracecat.dsl.common import (
    DSLEntrypoint,
    DSLInput,
    build_action_statements_from_actions,
)
from tracecat.dsl.view import (
    RFEdge,
    RFGraph,
    TriggerNode,
    TriggerNodeData,
    UDFNode,
    UDFNodeData,
)
from tracecat.expressions.expectations import ExpectedField, create_expectation_model
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.integrations.types import OAuthConnectionRef
from tracecat.registry.actions.schemas import (
    ActionStep,
    TemplateAction,
    TemplateActionDefinition,
)
from tracecat.workflow.store.schemas import RemoteWorkflowDefinition


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

    def test_normalize_is_noop_for_uuid_ids(self) -> None:
        """UUID action IDs and the trigger node are preserved, and the same
        graph instance is returned when no normalization is needed."""
        action1_uuid = uuid.uuid4()
        action2_uuid = uuid.uuid4()
        trigger_id = f"trigger-{uuid.uuid4()}"

        trigger = _make_trigger_node(trigger_id)
        node1 = _make_udf_node(action1_uuid)
        node2 = _make_udf_node(action2_uuid)

        graph = RFGraph(
            nodes=[trigger, node1, node2],
            edges=[
                RFEdge(source=trigger_id, target=node1.id),
                RFEdge(source=node1.id, target=node2.id),
            ],
        )

        normalized_graph = graph.normalize_action_ids()

        assert normalized_graph is graph
        assert normalized_graph.trigger.id == trigger_id
        assert [n.id for n in normalized_graph.action_nodes()] == [
            action1_uuid,
            action2_uuid,
        ]


def test_build_action_statements_preserves_date_like_string_input() -> None:
    """Date-like string inputs should stay strings for template arg validation."""
    template = TemplateAction(
        type="action",
        definition=TemplateActionDefinition(
            title="Date-like string input",
            description="Synthetic template for input parsing regression coverage.",
            name="date_like_string",
            namespace="testing",
            display_group="Testing",
            expects={
                "api_version": ExpectedField(
                    type="str",
                    description="A string that YAML can parse as a date.",
                ),
            },
            steps=[
                ActionStep(
                    ref="identity",
                    action="core.transform.reshape",
                    args={"value": "${{ inputs.api_version }}"},
                )
            ],
            returns="${{ steps.identity.result }}",
        ),
    )
    expectation_model = create_expectation_model(template.definition.expects)
    action = cast(
        Any,
        SimpleNamespace(
            id=uuid.uuid4(),
            ref="date_like_string",
            description="",
            type=template.definition.action,
            inputs="""
api_version: 2025-09-01
""",
            control_flow={},
            upstream_edges=[],
            is_interactive=False,
            interaction=None,
        ),
    )

    statements = build_action_statements_from_actions([action])

    assert len(statements) == 1
    assert statements[0].args["api_version"] == "2025-09-01"
    validated_args = expectation_model.model_validate(statements[0].args)
    assert validated_args.model_dump()["api_version"] == "2025-09-01"


_OAUTH_CONNECTION = {"provider_id": "github_app", "grant_type": "authorization_code"}


@pytest.mark.parametrize(
    "oauth_connection",
    [
        pytest.param(_OAUTH_CONNECTION, id="set"),
        pytest.param(None, id="unset"),
    ],
)
def test_oauth_connection_round_trips_to_synced_definition(
    oauth_connection: dict[str, str] | None,
) -> None:
    """The saved preference reaches the git-synced YAML, and is absent when unset."""
    action = cast(
        Any,
        SimpleNamespace(
            id=uuid.uuid4(),
            ref="get_repo",
            description="",
            type="tools.github.get_repository",
            inputs="owner: acme\n",
            control_flow={"oauth_connection": oauth_connection},
            upstream_edges=[],
            is_interactive=False,
            interaction=None,
        ),
    )

    [statement] = build_action_statements_from_actions([action])
    remote = RemoteWorkflowDefinition(
        id=WorkflowUUID.new(uuid.uuid4()).short(),
        definition=DSLInput(
            title="Synthetic workflow",
            description="",
            entrypoint=DSLEntrypoint(ref="get_repo"),
            actions=[statement],
        ),
    )
    [exported] = remote.model_dump(mode="json")["definition"]["actions"]

    if oauth_connection is None:
        assert statement.oauth_connection is None
        assert "oauth_connection" not in exported
    else:
        assert statement.oauth_connection == OAuthConnectionRef(
            provider_id="github_app", grant_type="authorization_code"
        )
        assert exported["oauth_connection"] == oauth_connection
        reimported = RemoteWorkflowDefinition.model_validate(
            remote.model_dump(mode="json")
        )
        assert reimported.definition.actions[0].oauth_connection == (
            statement.oauth_connection
        )
