from __future__ import annotations

from collections import defaultdict
from functools import cached_property
from typing import TYPE_CHECKING, Annotated, Any, Generic, Literal, Self, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    model_validator,
)
from pydantic.alias_generators import to_camel

from tracecat.db.schemas import Action
from tracecat.dsl.models import ActionStatement
from tracecat.identifiers import action
from tracecat.logging import logger
from tracecat.types.api import ActionControlFlow
from tracecat.types.exceptions import TracecatValidationError

if TYPE_CHECKING:
    from tracecat.db.schemas import Workflow


class Position(BaseModel):
    x: float = 0.0
    y: float = 0.0


class TSObject(BaseModel):
    """A model that holds a TypeScript Object information.

    Perks
    -----
    - Automatically serde to camelCase

    Important
    ---------
    - You must serialize with `by_alias=True` to get the camelCase keys.
    """

    model_config: ConfigDict = ConfigDict(
        extra="allow",
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class UDFNodeData(TSObject):
    is_configured: bool = False
    number_of_events: int = 0
    status: Literal["online", "offline"] = Field(default="offline")
    title: str = Field(description="Action title, used to generate the action ref")
    type: str = Field(description="UDF type")
    args: dict[str, Any] = Field(default_factory=dict, description="Action arguments")


class TriggerNodeData(TSObject):
    is_configured: bool = False
    status: Literal["online", "offline"] = Field(default="offline")
    title: str = Field(
        default="Trigger", description="Action title, used to generate the action ref"
    )
    webhook: dict[str, Any] = Field(default_factory=dict)
    schedules: list[dict[str, Any]] = Field(default_factory=list)


T = TypeVar("T", UDFNodeData, TriggerNodeData)


class RFNode(TSObject, Generic[T]):
    """Base React Flow Graph Node."""

    id: str = Field(..., description="RF Graph Node ID, not to confuse with action ref")
    type: Literal["trigger", "udf"]
    position: Position = Field(default_factory=Position)
    position_absolute: Position = Field(default_factory=Position)
    data: T

    @property
    def ref(self) -> str:
        return action.ref(self.data.title)


class TriggerNode(RFNode):
    """React Flow Graph Trigger Node."""

    type: Literal["trigger"] = Field(default="trigger", frozen=True)
    data: TriggerNodeData = Field(default_factory=TriggerNodeData)


class UDFNode(RFNode):
    """React Flow Graph Trigger Node."""

    type: Literal["udf"] = Field(default="udf", frozen=True)
    data: UDFNodeData


NodeVariant = TriggerNode | UDFNode
AnnotatedNodeVariant = Annotated[NodeVariant, Field(discriminator="type")]
NodeValidator: TypeAdapter[NodeVariant] = TypeAdapter(AnnotatedNodeVariant)


class RFEdge(TSObject):
    """React Flow Graph Edge."""

    id: str = Field(default=None)
    """RF Graph Edge ID. Not used in this context."""

    source: str
    """Source node ID."""

    target: str
    """Target node ID."""

    label: str | None = Field(default=None, description="Edge label")

    @model_validator(mode="before")
    def generate_id(cls, values):
        """Generate the ID as a concatenation of source and target with a prefix."""
        source = values.get("source")
        target = values.get("target")
        prefix = "reactflow__edge"
        if source and target:
            values["id"] = "-".join((prefix, source, target))
        return values


class RFGraph(TSObject):
    """React Flow Graph Object.

    Only used for workflow DSL construction.
    Helper class to describes a workflow's task connectivity as a directed graph.
    Has a bunch of helper methods to manipulate the graph.
    """

    nodes: list[RFNode] = Field(default_factory=list)
    edges: list[RFEdge] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_graph(self) -> Self:
        # The graph may have 0 inner edges if it only is a single node
        if not self.nodes:
            # NOTE: self.nodes includes all node types.
            # Having no action nodes is a valid graph state.
            # However, having no nodes at all is not valid.
            raise TracecatValidationError("Graph must have at least one node")
        try:
            _ = self.trigger
        except TracecatValidationError as e:
            raise e

        # Complex validations
        # No trigger edges in the main graph
        if not all(
            self.trigger.id not in (edge.source, edge.target)
            for edge in self.action_edges()
        ):
            # NOTE: We should not consider the trigger node as a source or target
            # in the main graph.
            raise TracecatValidationError(
                "Trigger node should not have edges in the main graph"
            )

        # Can't have one of the entrypoints as None
        if (self.logical_entrypoint is None) ^ (self.entrypoint is None):
            raise TracecatValidationError(
                "One of the logical and physical entrypoints are None:"
                f"({self.logical_entrypoint=}) != ({self.entrypoint=})",
            )

        # Check if the logical entrypoint matches the physical entrypoint
        if (
            self.logical_entrypoint
            and self.entrypoint
            and self.logical_entrypoint.ref != self.entrypoint.ref
        ):
            logger.error(
                f"Entrypoint doesn't match: {self.logical_entrypoint.ref!r} != {self.entrypoint.ref!r}"
            )
            raise TracecatValidationError("Entrypoint doesn't match")
        return self

    @property
    def trigger(self) -> TriggerNode:
        triggers = [node for node in self.nodes if node.type == "trigger"]
        if len(triggers) != 1:
            raise TracecatValidationError(
                f"Expected 1 trigger node, got {len(triggers)}"
            )
        return triggers[0]

    @cached_property
    def node_map(self) -> dict[str, RFNode]:
        return {node.id: node for node in self.nodes}

    @cached_property
    def adj_list(self) -> dict[str, list[str]]:
        """Return an adjacency list (node IDs) of the graph."""
        adj_list: dict[str, list[str]] = {node.id: [] for node in self.action_nodes()}
        for edge in self.action_edges():
            adj_list[edge.source].append(edge.target)
        return adj_list

    @cached_property
    def dep_list(self) -> dict[str, set[str]]:
        """Return a dependency list (node IDs) of the graph."""
        dep_list = defaultdict(set)
        for edge in self.action_edges():
            dep_list[edge.target].add(edge.source)
        return dep_list

    @cached_property
    def indegree(self) -> dict[str, int]:
        indegree: dict[str, int] = defaultdict(int)
        for edge in self.action_edges():
            indegree[edge.target] += 1
        return indegree

    @property
    def entrypoint(self) -> UDFNode | None:
        """The physical entrypoint of the graph. It is the node with the trigger as the source."""
        if len(self.action_nodes()) == 0:
            return None
        entrypoints = {
            edge.target for edge in self.edges if edge.source == self.trigger.id
        }
        if (n := len(entrypoints)) != 1:
            raise TracecatValidationError(
                f"Expected 1 physical entrypoint, got {n}: {entrypoints!r}"
            )
        return self.node_map[entrypoints.pop()]

    @property
    def logical_entrypoint(self) -> UDFNode | None:
        """The logical entrypoint of the graph. It is the node with no incoming edges when the
        graph is considered only with `udf` nodes."""
        act_nodes = self.action_nodes()
        if len(act_nodes) == 0:
            return None
        entrypoints = [node for node in act_nodes if self.indegree[node.id] == 0]
        if (n := len(entrypoints)) != 1:
            raise TracecatValidationError(
                f"Expected 1 logical entrypoint, got {n}: {entrypoints!r}"
            )
        return entrypoints[0]

    def action_edges(self) -> list[RFEdge]:
        """Return all edges that are not connected to the trigger node."""
        return [
            edge
            for edge in self.edges
            if self.trigger.id not in (edge.source, edge.target)
        ]

    def action_nodes(self) -> list[UDFNode]:
        """Return all `udf` (action) type nodes."""
        return [node for node in self.nodes if node.type == "udf"]

    def build_action_statements(self, actions: list[Action]) -> list[ActionStatement]:
        """Convert DB Actions into ActionStatements using the graph."""
        ref2action = {action.ref: action for action in actions}

        statements = []
        for node in self.action_nodes():
            dependencies = sorted(
                self.node_map[nid].ref for nid in self.dep_list[node.id]
            )

            action = ref2action[node.ref]
            control_flow = ActionControlFlow(
                run_if=action.control_flow.get("run_if"),
                for_each=action.control_flow.get("for_each"),
            )
            action_stmt = ActionStatement(
                id=action.id,
                ref=node.ref,
                action=node.data.type,
                args=action.inputs,
                depends_on=dependencies,
                run_if=control_flow.run_if,
                for_each=control_flow.for_each,
            )
            statements.append(action_stmt)
        return statements

    @classmethod
    def from_workflow(cls, workflow: Workflow) -> Self:
        if not workflow.object:
            raise ValueError("Empty response object")
        # This will accept either RFGraph or dict
        return cls.model_validate(workflow.object)

    def topsort_order(self) -> list[str]:
        from graphlib import TopologicalSorter

        ts = TopologicalSorter(self.dep_list)
        return list(ts.static_order())

    @staticmethod
    def with_defaults(workflow: Workflow) -> RFGraph:
        # Create a default graph object with only the webhook
        initial_data = {
            "nodes": [
                {
                    "id": f"trigger-{workflow.id}",
                    "type": "trigger",
                    "data": {
                        "type": "trigger",
                        "title": "Trigger",
                        "status": "offline",
                        "isConfigured": False,
                        "webhook": workflow.webhook,
                        "schedules": workflow.schedules or [],
                    },
                }
            ],
            "edges": [],
            "viewport": {"x": 0, "y": 0, "zoom": 1},
        }
        return RFGraph.model_validate(initial_data)
