from __future__ import annotations

from collections import defaultdict
from functools import cached_property
from typing import Any, Self

from pydantic import BaseModel, Field
from slugify import slugify

from tracecat.experimental.dsl.workflow import DSLInput
from tracecat.types.api import WorkflowResponse


def _to_slug(text: str) -> str:
    return slugify(text, separator="_")


def react_flow_graph_to_json(graph: RFGraph) -> dict[str, Any]:
    return react_flow_graph_to_dsl(graph).model_dump()


# NOTE: This function should be called alongside the actual get_workflow call
def react_flow_graph_to_dsl(graph: RFGraph, **kwargs: Any) -> DSLInput:
    """Convert a workflow React Flow graph object (WF-RFGO) into a workflow DSL (WF-IR)."""

    actions = []
    for node in graph.nodes:
        dependencies = sorted(
            graph.node_map[nid].ref for nid in graph.dep_list[node.id]
        )
        action = {
            "ref": node.ref,
            "action": node.data.type,
            "args": node.data.args,
            "depends_on": dependencies,
        }
        actions.append(action)
    dsl_input = {
        "entrypoint": graph.entrypoint,
        "actions": actions,
        **kwargs,
    }
    return DSLInput.model_validate(dsl_input)


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

    type: str
    """Namespaced action type."""

    data: RFNodeData

    @property
    def ref(self) -> str:
        return _to_slug(self.data.title)


class RFEdge(BaseModel):
    """React Flow Graph Edge."""

    id: str
    """RF Graph Edge ID. Not used in this context."""

    source: str
    """Source node ID."""

    target: str
    """Target node ID."""


class RFGraphObject(BaseModel):
    nodes: list[RFNode]
    edges: list[RFEdge]


class RFGraph:
    """React Flow Graph Object."""

    _raw: RFGraphObject

    def __init__(self, react_flow_obj: dict[str, Any], /):
        self._raw = RFGraphObject(**react_flow_obj)

    @property
    def nodes(self) -> list[RFNode]:
        return self._raw.nodes

    @property
    def edges(self) -> list[RFEdge]:
        return self._raw.edges

    @cached_property
    def node_map(self) -> dict[str, RFNode]:
        return {node.id: node for node in self._raw.nodes}

    @cached_property
    def adj_list(self) -> dict[str, list[str]]:
        adj_list: dict[str, list[str]] = {node.id: [] for node in self._raw.nodes}
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
        entrypoints = [
            node.id for node in self._raw.nodes if self.indegree[node.id] == 0
        ]
        if len(entrypoints) != 1:
            raise ValueError(
                f"Expected 1 entrypoint, got {len(entrypoints)}: {entrypoints!r}"
            )
        return entrypoints[0]

    @classmethod
    def from_response(cls, response: WorkflowResponse) -> Self:
        if not response.object:
            raise ValueError("Empty response object")
        return cls(response.object)

    def topsort_order(self) -> list[str]:
        from graphlib import TopologicalSorter

        ts = TopologicalSorter(self.dep_list)
        return list(ts.static_order())
