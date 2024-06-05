from __future__ import annotations

from collections import defaultdict
from functools import cached_property
from typing import TYPE_CHECKING, Any, Literal, Self

from loguru import logger
from pydantic import BaseModel, Field, model_validator
from slugify import slugify

from tracecat.dsl.workflow import ActionStatement
from tracecat.types.exceptions import TracecatValidationError

if TYPE_CHECKING:
    from tracecat.db.schemas import Workflow


def get_ref(text: str) -> str:
    return slugify(text, separator="_")


class RFNodeData(BaseModel):
    """React Flow Graph Node Data."""

    type: str
    """Namespaced action type."""

    title: str
    """Action title. Used to generate the action ref."""

    args: dict[str, Any] = Field(default_factory=dict)
    """Action arguments."""


class RFNode(BaseModel):
    """React Flow Graph Node."""

    id: str
    """RF Graph Node ID. Different from action ref."""

    type: Literal["udf"]
    """UDF type."""

    data: RFNodeData

    @property
    def ref(self) -> str:
        return get_ref(self.data.title)


class RFTriggerNodeData(BaseModel):
    """React Flow Trigger Node Data."""

    title: str
    """Node title. Used to generate the action ref."""


class RFTriggerNode(BaseModel):
    """React Flow Trigger Node."""

    id: str
    """RF Graph Node ID. Different from action ref."""

    type: Literal["trigger"]
    """Namespaced action type."""

    data: RFTriggerNodeData

    @property
    def ref(self) -> str:
        return get_ref(self.data.title)


class RFEdge(BaseModel):
    """React Flow Graph Edge."""

    id: str
    """RF Graph Edge ID. Not used in this context."""

    source: str
    """Source node ID."""

    target: str
    """Target node ID."""


class RFGraph(BaseModel):
    """React Flow Graph Object.

    Only used for workflow DSL construction.
    Helper class to describes a workflow's task connectivity as a directed graph.
    Has a bunch of helper methods to manipulate the graph.
    """

    nodes: list[RFNode]
    edges: list[RFEdge]
    trigger: RFTriggerNode
    entrypoint_id: str

    @model_validator(mode="after")
    def validate_graph(self) -> Self:
        # The graph may have 0 inner edges if it only is a single node
        if not self.nodes:
            raise TracecatValidationError(
                "Graph must have at least one node excluding trigger"
            )
        if not self.trigger:
            raise TracecatValidationError("Graph must have a trigger node")

        # Complex validations
        # No trigger edges in the main graph
        if not all(
            self.trigger.id not in (edge.source, edge.target) for edge in self.edges
        ):
            raise TracecatValidationError(
                "Trigger node should not have edges in the main graph"
            )

        if self.entrypoint != self.node_map[self.entrypoint_id].ref:
            lhs = self.entrypoint
            rhs = self.node_map[self.entrypoint_id].ref
            logger.error(f"Entrypoint doesn't match: {lhs!r} != {rhs!r}")
            raise TracecatValidationError("Entrypoint doesn't match")
        return self

    @cached_property
    def node_map(self) -> dict[str, RFNode]:
        return {node.id: node for node in self.nodes}

    @cached_property
    def adj_list(self) -> dict[str, list[str]]:
        adj_list: dict[str, list[str]] = {node.id: [] for node in self.nodes}
        for edge in self.edges:
            adj_list[edge.source].append(edge.target)
        return adj_list

    @cached_property
    def dep_list(self) -> dict[str, set[str]]:
        dep_list = defaultdict(set)
        for edge in self.edges:
            dep_list[edge.target].add(edge.source)
        return dep_list

    @cached_property
    def indegree(self) -> dict[str, int]:
        indegree: dict[str, int] = defaultdict(int)
        for edge in self.edges:
            indegree[edge.target] += 1
        return indegree

    @property
    def entrypoint(self) -> str:
        entrypoints = [node.ref for node in self.nodes if self.indegree[node.id] == 0]
        if len(entrypoints) != 1:
            raise TracecatValidationError(
                f"Expected 1 entrypoint, got {len(entrypoints)}: {entrypoints!r}"
            )
        return entrypoints[0]

    def action_statements(self, workflow: Workflow) -> list[ActionStatement]:
        if len(self.nodes) != len(workflow.actions):
            raise ValueError("Mismatch between graph nodes and workflow actions")

        actions = workflow.actions or []
        action_map = {action.ref: action for action in actions}

        statements = []
        for node in self.nodes:
            dependencies = sorted(
                self.node_map[nid].ref for nid in self.dep_list[node.id]
            )

            action = action_map[node.ref]
            action_stmt = ActionStatement(
                ref=node.ref,
                action=node.data.type,
                args=action.inputs,
                depends_on=dependencies,
            )
            statements.append(action_stmt)
        return statements

    @classmethod
    def from_dict(cls, obj: dict[str, Any], /) -> Self:
        triggers, nodes = [], []
        for node in obj["nodes"]:
            t = node["type"]
            if t == "trigger":
                triggers.append(RFTriggerNode(**node))
            elif t == "udf":
                nodes.append(RFNode(**node))
            else:
                raise ValueError(f"Unknown node type: {t!r}")

        if len(triggers) != 1:
            raise TracecatValidationError(
                f"Expected 1 trigger node, got {len(triggers)}"
            )

        trigger = triggers[0]
        entrypoint = []
        edges = []
        for edge in obj["edges"]:
            if trigger.id in (edge["source"], edge["target"]):
                entrypoint.append(edge)
            else:
                edges.append(RFEdge(**edge))

        if len(entrypoint) != 1:
            raise TracecatValidationError(
                f"Expected 1 trigger edge, got {len(entrypoint)}"
            )

        return cls(
            nodes=nodes,
            edges=edges,
            trigger=trigger,
            entrypoint_id=entrypoint[0]["target"],
        )

    @classmethod
    def from_workflow(cls, workflow: Workflow) -> Self:
        if not workflow.object:
            raise ValueError("Empty response object")
        if not workflow.actions:
            raise ValueError(
                "Empty actions list. Please hydrate the workflow by "
                "calling `workflow.actions` inside an open db session."
            )
        return cls.from_dict(workflow.object)

    def topsort_order(self) -> list[str]:
        from graphlib import TopologicalSorter

        ts = TopologicalSorter(self.dep_list)
        return list(ts.static_order())
