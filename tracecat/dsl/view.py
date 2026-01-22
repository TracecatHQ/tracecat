from __future__ import annotations

from collections import defaultdict
from functools import cached_property
from typing import TYPE_CHECKING, Annotated, Any, Literal, Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    model_validator,
)
from pydantic.alias_generators import to_camel

from tracecat.dsl.enums import EdgeType
from tracecat.exceptions import TracecatValidationError
from tracecat.identifiers import ActionID
from tracecat.identifiers.action import ActionUUID

if TYPE_CHECKING:
    from tracecat.db.models import Action, Workflow


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

    model_config = ConfigDict(
        extra="allow",
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class UDFNodeData(TSObject):
    is_configured: bool = False
    number_of_events: int = 0
    status: Literal["online", "offline"] = Field(default="offline")
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


class BaseRFNode[T: (UDFNodeData | TriggerNodeData)](TSObject):
    """Base React Flow Graph Node."""

    position: Position = Field(default_factory=Position)
    position_absolute: Position = Field(default_factory=Position)
    data: T


class TriggerNode(BaseRFNode[TriggerNodeData]):
    """React Flow Graph Trigger Node."""

    type: Literal["trigger"] = Field(default="trigger", frozen=True)
    id: str = Field(
        ...,
        description="RF Graph Node ID",
        pattern=r"^trigger-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    )
    data: TriggerNodeData = Field(default_factory=TriggerNodeData)


class UDFNode(BaseRFNode[UDFNodeData]):
    """React Flow Graph UDF Node."""

    type: Literal["udf"] = Field(default="udf", frozen=True)
    id: ActionID = Field(..., description="Action ID")
    data: UDFNodeData


NodeVariant = TriggerNode | UDFNode
AnnotatedNodeVariant = Annotated[NodeVariant, Field(discriminator="type")]
NodeValidator: TypeAdapter[NodeVariant] = TypeAdapter(AnnotatedNodeVariant)


class RFEdge(TSObject):
    """React Flow Graph Edge."""

    id: str | None = Field(default=None, description="RF Graph Edge ID")
    """RF Graph Edge ID. Not used in this context."""

    source: str | ActionID
    """Source node ID (action ID or trigger ID)."""

    target: str | ActionID
    """Target node ID (action ID or trigger ID)."""

    label: str | None = Field(default=None, description="Edge label")

    source_handle: EdgeType | None = Field(
        default=None, description="Edge source handle type"
    )

    @model_validator(mode="before")
    def generate_id(cls, values: dict[str, Any]) -> dict[str, Any]:
        """Generate the ID as a concatenation of source and target with a prefix."""
        if (source := values.get("source")) and (target := values.get("target")):
            values["id"] = "-".join(("reactflow__edge", str(source), str(target)))
        return values


class RFGraph(TSObject):
    """React Flow Graph Object.

    Only used for workflow DSL construction.
    Helper class to describes a workflow's task connectivity as a directed graph.
    Has a bunch of helper methods to manipulate the graph.
    """

    nodes: list[NodeVariant] = Field(default_factory=list)
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
    def node_map(self) -> dict[str | ActionID, NodeVariant]:
        return {node.id: node for node in self.nodes}

    @cached_property
    def adj_list(self) -> dict[ActionID, list[ActionID]]:
        """Return an adjacency list (action node IDs) of the graph.

        Note: Only includes action-to-action edges, not trigger edges.
        """
        adj: dict[ActionID, list[ActionID]] = {
            node.id: [] for node in self.action_nodes()
        }
        for edge in self.action_edges():
            # action_edges only contains action-to-action edges (no trigger)
            # so source and target are guaranteed to be ActionID
            source = ActionID(str(edge.source))
            target = ActionID(str(edge.target))
            adj[source].append(target)
        return adj

    @cached_property
    def dep_list(self) -> dict[ActionID, set[ActionID]]:
        """Return a dependency list (action node IDs) of the graph.

        Note: Only includes action-to-action edges, not trigger edges.
        """
        deps: dict[ActionID, set[ActionID]] = defaultdict(set)
        for edge in self.action_edges():
            source = ActionID(str(edge.source))
            target = ActionID(str(edge.target))
            deps[target].add(source)
        return deps

    @cached_property
    def indegree(self) -> dict[ActionID, int]:
        """Return indegree count for action nodes."""
        deg: dict[ActionID, int] = defaultdict(int)
        for edge in self.action_edges():
            target = ActionID(str(edge.target))
            deg[target] += 1
        return deg

    @property
    def entrypoints(self) -> list[UDFNode]:
        """Return all entrypoints of the graph."""
        act_nodes = self.action_nodes()
        return [node for node in act_nodes if self.indegree[node.id] == 0]

    def action_edges(self) -> list[RFEdge]:
        """Return all edges that are not connected to the trigger node."""
        return [
            edge
            for edge in self.edges
            if self.trigger.id not in (edge.source, edge.target)
        ]

    def action_nodes(self) -> list[UDFNode]:
        """Return all `udf` (action) type nodes."""
        return cast(list[UDFNode], [node for node in self.nodes if node.type == "udf"])

    @classmethod
    def from_actions(cls, workflow: Workflow, actions: list[Action]) -> Self:
        """Build RFGraph from Actions (single source of truth).

        This method constructs the React Flow graph from Action records,
        making Actions the authoritative source for graph structure.
        """
        # Build trigger node from workflow
        trigger_node = TriggerNode(
            id=f"trigger-{workflow.id}",
            position=Position(
                x=workflow.trigger_position_x,
                y=workflow.trigger_position_y,
            ),
            data=TriggerNodeData(
                status="online" if workflow.status == "online" else "offline",
            ),
        )

        nodes: list[NodeVariant] = [trigger_node]
        edges: list[RFEdge] = []

        for act in actions:
            # Build action node
            node = UDFNode(
                id=act.id,
                position=Position(x=act.position_x, y=act.position_y),
                data=UDFNodeData(type=act.type),
            )
            nodes.append(node)

            # Build edges from upstream_edges (explicit edge storage)
            for edge_data in act.upstream_edges or []:
                source_type = edge_data.get("source_type", "udf")
                edges.append(
                    RFEdge(
                        source=edge_data["source_id"],
                        target=act.id,
                        source_handle=(
                            EdgeType(edge_data.get("source_handle", "success"))
                            if source_type == "udf"
                            else None
                        ),
                        label="Trigger" if source_type == "trigger" else None,
                    )
                )

        return cls(nodes=nodes, edges=edges)

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
                    },
                }
            ],
            "edges": [],
            "viewport": {"x": 0, "y": 0, "zoom": 1},
        }
        return RFGraph.model_validate(initial_data)

    def normalize_action_ids(self) -> Self:
        """Normalize all action node IDs to canonical UUID string format.

        This handles backward compatibility with different action ID formats
        that may exist in the workflow graph (legacy prefixed IDs like `act-<32hex>`).

        Returns:
            Self: A new RFGraph with normalized action IDs.
        """
        # Build mapping of old IDs to normalized IDs for UDF nodes only
        id_mapping: dict[str, str] = {}
        for node in self.nodes:
            if node.type != "udf":
                continue
            try:
                old_id = str(node.id)
                normalized = str(ActionUUID.new(node.id))
                if normalized != old_id:
                    id_mapping[old_id] = normalized
            except ValueError:
                # Skip nodes with invalid IDs - they'll be caught by validation elsewhere
                continue

        # If no normalization needed, return self
        if not id_mapping:
            return self

        # Create new nodes with normalized IDs
        new_nodes: list[NodeVariant] = []
        for node in self.nodes:
            node_id_str = str(node.id)
            if node.type == "udf" and node_id_str in id_mapping:
                # Create a copy with the normalized ID
                node_data = node.model_dump(by_alias=True)
                node_data["id"] = id_mapping[node_id_str]
                new_nodes.append(UDFNode.model_validate(node_data))
            else:
                new_nodes.append(node)

        # Create new edges with normalized source/target IDs
        new_edges: list[RFEdge] = []
        for edge in self.edges:
            edge_data = edge.model_dump(by_alias=True, exclude={"id"})
            source_str = str(edge.source)
            target_str = str(edge.target)
            if source_str in id_mapping:
                edge_data["source"] = id_mapping[source_str]
            if target_str in id_mapping:
                edge_data["target"] = id_mapping[target_str]
            new_edges.append(RFEdge.model_validate(edge_data))

        return self.model_copy(update={"nodes": new_nodes, "edges": new_edges})
