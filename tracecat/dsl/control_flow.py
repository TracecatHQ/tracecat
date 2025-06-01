from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, Field

from tracecat.dsl.common import AdjDst, DSLInput, edge_components_from_dep
from tracecat.expressions.validation import ExpressionStr


class ExplodeArgs(BaseModel):
    collection: ExpressionStr = Field(..., description="The collection to explode")


class ImplodeArgs(BaseModel):
    """Arguments for implode operations"""

    items: ExpressionStr = Field(..., description="The jsonpath to select items from")
    drop_nulls: bool = Field(
        default=False, description="Whether to drop null values from the final result"
    )


@dataclass(slots=True)
class ScopeBoundary:
    """Information about a discovered explode/implode boundary"""

    explode_ref: str
    implode_ref: str | None  # None if no matching implode found
    scoped_tasks: set[str]
    """Task refs in this scope"""

    scope_id: str
    """The unique scope identifier for this explode/implode pair"""


class ScopeAwareEdgeMarker(StrEnum):
    """Enhanced edge markers for scope-aware scheduling"""

    PENDING = "pending"
    VISITED = "visited"
    SKIPPED = "skipped"
    SCOPE_BOUNDARY = "scope_boundary"  # For explode->task edges


class ScopeAnalyzer:
    """Analyzes DSL graphs to discover explode/implode scope boundaries"""

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
    #     """Find all explode/implode pairs and their enclosed tasks"""
    #     implode_tasks: dict[str, str] = {}
    #     for task in self.dsl.actions:
    #         if task.action == PlatformAction.TRANSFORM_IMPLODE:
    #             args = ImplodeArgs(**task.args)
    #             implode_tasks[args.path] = task.ref

    #     scope_boundaries: dict[str, ScopeBoundary] = {}

    #     for task in self.dsl.actions:
    #         if task.action != PlatformAction.TRANSFORM_EXPLODE:
    #             continue

    #         # Get explode args to find scope ID
    #         args = ExplodeArgs(**task.args)
    #         # Find corresponding implode(s) that use this scope ID
    #         implode_ref = implode_tasks.get(scope_id)

    #         # Find all tasks between explode and implode
    #         if implode_ref:
    #             scoped_tasks = self._find_tasks_between(task.ref, implode_ref)
    #         else:
    #             # If no implode found, include all reachable tasks from explode
    #             scoped_tasks = self._find_all_reachable_tasks(task.ref)

    #         scope_boundaries[task.ref] = ScopeBoundary(
    #             explode_ref=task.ref,
    #             implode_ref=implode_ref,
    #             scoped_tasks=scoped_tasks,
    #             scope_id=scope_id,
    #         )

    #     return scope_boundaries

    def _find_tasks_between(self, explode_ref: str, implode_ref: str) -> set[str]:
        """Find all tasks reachable from explode but not beyond implode"""
        visited = set()
        stack = [explode_ref]

        while stack:
            current = stack.pop()
            if current in visited or current == implode_ref:
                continue

            visited.add(current)

            # Add all children to stack
            for child_ref, _ in self.adj.get(current, []):
                if child_ref not in visited:
                    stack.append(child_ref)

        # Remove the explode task itself, include everything up to but not including implode
        visited.discard(explode_ref)
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
