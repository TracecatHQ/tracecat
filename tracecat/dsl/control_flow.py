from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from tracecat.dsl.common import AdjDst, DSLInput, edge_components_from_dep
from tracecat.dsl.enums import StreamErrorHandlingStrategy
from tracecat.expressions.validation import ExpressionStr


class ScatterArgs(BaseModel):
    collection: Any = Field(..., description="The collection to scatter")


class GatherArgs(BaseModel):
    """Arguments for gather operations"""

    items: ExpressionStr = Field(..., description="The jsonpath to select items from")
    drop_nulls: bool = Field(
        default=False, description="Whether to drop null values from the final result"
    )
    error_handling_strategy: StreamErrorHandlingStrategy = Field(
        default=StreamErrorHandlingStrategy.PARTITION
    )


@dataclass(slots=True)
class ScopeBoundary:
    """Information about a discovered scatter/gather boundary"""

    scatter_ref: str
    gather_ref: str | None  # None if no matching gather found
    scoped_tasks: set[str]
    """Task refs in this scope"""

    scope_id: str
    """The unique scope identifier for this scatter/gather pair"""


class ScopeAwareEdgeMarker(StrEnum):
    """Enhanced edge markers for scope-aware scheduling"""

    PENDING = "pending"
    VISITED = "visited"
    SKIPPED = "skipped"
    SCOPE_BOUNDARY = "scope_boundary"  # For scatter->task edges


class ScopeAnalyzer:
    """Analyzes DSL graphs to discover scatter/gather scope boundaries"""

    def __init__(self, dsl: DSLInput):
        self.dsl = dsl
        self.tasks = {task.ref: task for task in dsl.actions}
        self.adj = self._build_adjacency()

    def _build_adjacency(self) -> dict[str, set[AdjDst]]:
        """Build adjacency list from DSL actions"""
        adj: dict[str, set[AdjDst]] = {}
        for task in self.dsl.actions:
            adj[task.ref] = set()
            for dep_ref in task.depends_on:
                src_ref, edge_type = edge_components_from_dep(dep_ref)
                if src_ref not in adj:
                    adj[src_ref] = set()
                adj[src_ref].add((task.ref, edge_type))
        return adj

    # def discover_scope_boundaries(self) -> dict[str, ScopeBoundary]:
    #     """Find all scatter/gather pairs and their enclosed tasks"""
    #     gather_tasks: dict[str, str] = {}
    #     for task in self.dsl.actions:
    #         if task.action == PlatformAction.TRANSFORM_IMPLODE:
    #             args = GatherArgs(**task.args)
    #             gather_tasks[args.path] = task.ref

    #     scope_boundaries: dict[str, ScopeBoundary] = {}

    #     for task in self.dsl.actions:
    #         if task.action != PlatformAction.TRANSFORM_EXPLODE:
    #             continue

    #         # Get scatter args to find scope ID
    #         args = ScatterArgs(**task.args)
    #         # Find corresponding gather(s) that use this scope ID
    #         gather_ref = gather_tasks.get(scope_id)

    #         # Find all tasks between scatter and gather
    #         if gather_ref:
    #             scoped_tasks = self._find_tasks_between(task.ref, gather_ref)
    #         else:
    #             # If no gather found, include all reachable tasks from scatter
    #             scoped_tasks = self._find_all_reachable_tasks(task.ref)

    #         scope_boundaries[task.ref] = ScopeBoundary(
    #             scatter_ref=task.ref,
    #             gather_ref=gather_ref,
    #             scoped_tasks=scoped_tasks,
    #             scope_id=scope_id,
    #         )

    #     return scope_boundaries

    def _find_tasks_between(self, scatter_ref: str, gather_ref: str) -> set[str]:
        """Find all tasks reachable from scatter but not beyond gather"""
        visited = set()
        stack = [scatter_ref]

        while stack:
            current = stack.pop()
            if current in visited or current == gather_ref:
                continue

            visited.add(current)

            # Add all children to stack
            for child_ref, _ in self.adj.get(current, []):
                if child_ref not in visited:
                    stack.append(child_ref)

        # Remove the scatter task itself, include everything up to but not including gather
        visited.discard(scatter_ref)
        return visited

    def _find_all_reachable_tasks(self, start_ref: str) -> set[str]:
        """Find all tasks reachable from start_ref"""
        visited = set()
        stack = [start_ref]

        while stack:
            current = stack.pop()
            if current in visited:
                continue

            visited.add(current)

            # Add all children to stack
            for child_ref, _ in self.adj.get(current, []):
                if child_ref not in visited:
                    stack.append(child_ref)

        # Remove the start task itself
        visited.discard(start_ref)
        return visited
