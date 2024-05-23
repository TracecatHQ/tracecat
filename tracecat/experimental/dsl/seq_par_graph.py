# type: ignore
"""This code is unused."""

from collections import defaultdict
from collections.abc import Iterable
from functools import cached_property
from graphlib import TopologicalSorter
from typing import Any

from pydantic import BaseModel, Field

from tracecat.experimental.dsl.seq_par_workflow import (
    ActivityStatement,
    DSLInput,
    ParallelStatement,
    SequentialStatement,
    Statement,
)
from tracecat.types.api import WorkflowResponse


def parse_react_flow_graph_as_ir(response: WorkflowResponse) -> DSLInput:
    """Convert a workflow React Flow graph object (WF-RFGO) into a workflow IR (WF-IR).

    Parameters
    ----------
    response : WorkflowResponse
        The RF workflow response object.

    Output
    ------
    WF-IR as WF-JSON (interchangeable with WF-YAML)

    Implementation
    --------------
    1. Need to figure out which nodes are the roots. Assume there's only 1 root for now.
    2. Treat each node initially as a


    """
    if not response.object:
        raise ValueError("No object found in the response.")
    g = RFGraph(response.object)
    dsl_input = parse_dag_to_blocks(g)
    return dsl_input


def topological_sort(adj_list: dict[str, set[str]]) -> Iterable[str]:
    def invert_adj_list(adj_list: dict[str, set[str]]) -> dict[str, set[str]]:
        # Convert the adjacency list to a dependency list
        dep_list = defaultdict(set)
        for node, neighbors in adj_list.items():
            for neighbor in neighbors:
                dep_list[neighbor].add(node)
        # Ensure all nodes are in the dependency list, even if they have no dependencies
        for node in adj_list:
            if node not in dep_list:
                dep_list[node] = set()
        return dep_list

    dep_list = invert_adj_list(adj_list)
    topological_sorter = TopologicalSorter(dep_list)

    return topological_sorter.static_order()


class RFNodeData(BaseModel):
    type: str
    title: str
    args: dict[str, Any] = Field(default_factory=dict)


class RFNode(BaseModel):
    id: str
    type: str
    data: RFNodeData


class RFEdge(BaseModel):
    id: str
    source: str
    target: str


class RFGraphObject(BaseModel):
    nodes: list[RFNode]
    edges: list[RFEdge]


class RFGraph:
    """React Flow Graph Object."""

    # Graph object
    _raw: RFGraphObject

    # Override the default constructor
    def __init__(self, react_flow_obj: dict[str, Any], /):
        self._raw = RFGraphObject(**react_flow_obj)

    @cached_property
    def nodes(self) -> dict[str, RFNode]:
        nodes = {node.id: node for node in self._raw.nodes}
        return nodes

    @property
    def edges(self) -> list[RFEdge]:
        return self._raw.edges

    @cached_property
    def adj(self) -> dict[str, list[str]]:
        adj_list = {node.id: [] for node in self._raw.nodes}
        for edge in self.edges:
            adj_list[edge.source].append(edge.target)
        return adj_list

    @cached_property
    def indegree(self) -> dict[str, int]:
        indegree = defaultdict(int)
        for edge in self.edges:
            indegree[edge.target] += 1
        return indegree

    @cached_property
    def topsort_order(self) -> list[str]:
        return topological_sort(self.adj)


def parse_dag_to_blocks(g: RFGraph) -> DSLInput:
    visited = set()

    def visit(node_id: str, *, seq: list[Statement]) -> None:
        if node_id in visited:
            return
        # Process current node
        visited.add(node_id)
        node = g.nodes[node_id]
        curr = ActivityStatement(
            ref=node_id, action=node.data.type, args=node.data.args
        )
        seq.append(curr)

        # Process child nodes
        children = g.adj[node_id]
        if len(children) == 0:
            # Leaf node: we're done here
            return
        elif len(children) == 1:
            # Find the whole sequential block. Either:
            # 1. Iterate through the children until we find a join/fork
            # 2. Recurse on the child and flatten the nested sequential blocks

            # Need to get a list of child statements and place it in a sequential block
            child_id = children[0]
            # perform indegree check to see if the child is a join node.
            if g.indegree[child_id] > 1:
                # Next node is a join node
                # Break recursion here, and continue using topsort order
                return
            # Visit the next node, with the current sequential block
            visit(child_id, seq=seq)

        else:
            # Parallel node
            # Create new sequences
            branches = []
            for child_id in children:
                child_seq = []
                visit(child_id, seq=child_seq)
                branches.append(SequentialStatement(sequence=child_seq))
            seq.append(ParallelStatement(parallel=branches))

    root_seq: list[Statement] = []
    for node in g.topsort_order:
        visit(node, seq=root_seq)

    root_stmt = SequentialStatement(sequence=root_seq)
    variables = {}

    return DSLInput(root=root_stmt, variables=variables)
