"""Tracecat DSL Common Module."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import Any, Literal, NotRequired, Self, TypedDict

import orjson
import temporalio.api.common.v1
import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError
from temporalio import workflow
from temporalio.common import RetryPolicy, TypedSearchAttributes
from temporalio.exceptions import ApplicationError, ChildWorkflowError, FailureError

from tracecat.auth.types import Role
from tracecat.db.models import Action
from tracecat.dsl._converter import PydanticPayloadConverter
from tracecat.dsl.enums import (
    EdgeType,
    FailStrategy,
    LoopStrategy,
    PlatformAction,
    WaitStrategy,
)
from tracecat.dsl.schemas import (
    ROOT_STREAM,
    ActionStatement,
    DSLConfig,
    DSLEnvironment,
    DSLExecutionError,
    ExecutionContext,
    RunContext,
    StreamID,
    TaskResult,
    Trigger,
)
from tracecat.dsl.view import (
    NodeVariant,
    RFEdge,
    RFGraph,
    TriggerNode,
    UDFNode,
    UDFNodeData,
)
from tracecat.exceptions import (
    TracecatCredentialsError,
    TracecatDSLError,
    TracecatException,
    TracecatExpressionError,
    TracecatValidationError,
)
from tracecat.expressions.common import ExprContext
from tracecat.expressions.core import extract_expressions
from tracecat.expressions.expectations import ExpectedField
from tracecat.identifiers import ActionID
from tracecat.identifiers.schedules import ScheduleUUID
from tracecat.identifiers.workflow import AnyWorkflowID, WorkflowUUID
from tracecat.interactions.schemas import ActionInteractionValidator
from tracecat.logger import logger
from tracecat.registry.lock.types import RegistryLock
from tracecat.storage.object import CollectionObject, InlineObject, StoredObject
from tracecat.workflow.actions.schemas import ActionControlFlow
from tracecat.workflow.executions.enums import (
    ExecutionType,
    TemporalSearchAttr,
    TriggerType,
)

_memo_payload_converter = PydanticPayloadConverter()


class UpstreamEdgeData(TypedDict):
    """Type definition for upstream edge data stored in Action.upstream_edges.

    This represents a single edge connecting a source node to a target action.
    The source_id is required, while source_type and source_handle are optional.
    """

    source_id: str
    """The ID of the source node (action UUID or trigger ID)."""

    source_type: NotRequired[Literal["trigger", "udf"]]
    """The type of the source node."""

    source_handle: NotRequired[Literal["success", "error"]]
    """The edge type, defaults to 'success' if not specified."""


UpstreamEdgeDataValidator = TypeAdapter(UpstreamEdgeData)
"""TypeAdapter for validating upstream edge data at runtime."""


class DSLEntrypoint(BaseModel):
    ref: str | None = Field(default=None, description="The entrypoint action ref")
    expects: dict[str, ExpectedField] | None = Field(
        default=None,
        description=(
            "Expected trigger input schema. "
            "Use this to specify the expected shape of the trigger input."
        ),
    )
    """Trigger input schema."""


def key2loc(key: str) -> str:
    return "inputs" if key == "args" else key


SCOPE_OPENER_ACTIONS: frozenset[str] = frozenset(
    (
        PlatformAction.TRANSFORM_SCATTER,
        PlatformAction.LOOP_START,
    )
)

SCOPE_CLOSER_ACTIONS: frozenset[str] = frozenset(
    (
        PlatformAction.TRANSFORM_GATHER,
        PlatformAction.LOOP_END,
    )
)

CLOSER_TO_OPENER_ACTION: dict[str, str] = {
    PlatformAction.TRANSFORM_GATHER: PlatformAction.TRANSFORM_SCATTER,
    PlatformAction.LOOP_END: PlatformAction.LOOP_START,
}


class DSLInput(BaseModel):
    """DSL definition for a workflow.

    The difference between this and a normal workflow engine is that here,
    our workflow execution order is defined by the DSL itself, independent
    of a workflow scheduler.

    With a traditional
    This allows the execution of the workflow to be fully deterministic.
    """

    # Using this for backwards compatibility of existing workflow definitions
    model_config = ConfigDict(extra="ignore")
    title: str
    description: str
    entrypoint: DSLEntrypoint
    actions: list[ActionStatement]
    config: DSLConfig = Field(default_factory=DSLConfig)
    triggers: list[Trigger] = Field(default_factory=list)
    returns: Any | None = Field(
        default=None, description="The action ref or value to return."
    )
    error_handler: str | None = Field(
        default=None, description="The action ref to handle errors."
    )

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        kwargs["mode"] = "json"
        return super().model_dump(*args, **kwargs)

    @model_validator(mode="after")
    def validate_structure(self) -> Self:
        if not self.actions:
            raise TracecatDSLError("At least one action must be defined.")
        if len({action.ref for action in self.actions}) != len(self.actions):
            counter = {}
            for action in self.actions:
                counter[action.ref] = counter.get(action.ref, 0) + 1
            duplicates = ", ".join(f"{k!r}" for k, v in counter.items() if v > 1)
            raise TracecatDSLError(
                "All action references (the action title in snake case) must be unique."
                f" Duplicate refs: {duplicates}"
            )
        n_entrypoints = sum(1 for action in self.actions if not action.depends_on)
        if n_entrypoints == 0:
            raise TracecatDSLError("No entrypoints found")

        # Validate that all the refs in depends_on are valid actions
        valid_action_refs = {a.ref for a in self.actions}
        # Actions refs can now contain a path, so we need to check for that
        dependencies = set()
        for a in self.actions:
            for dep in a.depends_on:
                try:
                    src, _ = edge_components_from_dep(dep)
                except ValueError:
                    raise TracecatDSLError(
                        f"Invalid depends_on ref: {dep!r} in action {a.ref!r}"
                    ) from None
                dependencies.add(src)
        invalid_deps = dependencies - valid_action_refs
        if invalid_deps:
            raise TracecatDSLError(
                f"Invalid depends_on refs in actions: {invalid_deps}."
                f" Valid actions: {valid_action_refs}"
            )

        self._all_action_expressions_valid(valid_action_refs)
        self._validate_scatter_gather_scopes()
        return self

    def _all_action_expressions_valid(self, valid_action_refs: set[str]) -> None:
        """Validate that all action expressions are valid."""
        for action in self.actions:
            for key, value in action.model_dump().items():
                expr_ctxs = extract_expressions(value)
                for dep in expr_ctxs[ExprContext.ACTIONS]:
                    if dep not in valid_action_refs:
                        raise TracecatDSLError(
                            f"Action '{action.ref}' has an expression in field '{key2loc(key)}' that references unknown action '{dep}'"
                        )

    def _validate_scatter_gather_scopes(self) -> None:
        """Validate control-flow scope boundaries.

        Logic
        -----
        - We need to map all actions to a scope.
        - Traverse the graph and map out the scopes
        - Outer scope ACTIONS cannot reference inner scope ACTIONS
        - We need to validate that no actions outside the scope reference actions inside the scope.
        - We need to validate that no actions outside the scope reference actions inside the scope.
        """
        # Find scope open and close actions
        scatter_actions = []
        gather_actions = []
        loop_start_actions = []
        loop_end_actions = []
        for action in self.actions:
            if action.action == PlatformAction.TRANSFORM_SCATTER:
                scatter_actions.append(action.ref)
            elif action.action == PlatformAction.TRANSFORM_GATHER:
                gather_actions.append(action.ref)
            elif action.action == PlatformAction.LOOP_START:
                loop_start_actions.append(action.ref)
            elif action.action == PlatformAction.LOOP_END:
                loop_end_actions.append(action.ref)

        if len(gather_actions) > len(scatter_actions):
            raise TracecatDSLError(
                "There are more gather actions than scatter actions."
            )
        if len(loop_end_actions) != len(loop_start_actions):
            raise TracecatDSLError(
                "Loop scopes must be balanced: "
                f"found {len(loop_start_actions)} loop start action(s) and "
                f"{len(loop_end_actions)} loop end action(s)."
            )

        if not scatter_actions and not loop_start_actions:
            return  # No scope actions, no scope validation needed

        # Build adjacency list for graph traversal
        adj = self._to_adjacency()

        # Assign scope IDs to all actions
        scopes, scope_hierarchy, scope_openers = self._assign_action_scopes(adj)
        self._validate_scope_dependencies(scopes, scope_hierarchy, scope_openers)
        self._validate_loop_scope_synchronization(
            action_scopes=scopes,
            scope_hierarchy=scope_hierarchy,
            scope_openers=scope_openers,
        )

    def _validate_scope_dependencies(
        self,
        action_scopes: dict[str, str],
        scope_hierarchy: dict[str, str | None],
        scope_openers: dict[str, str],
    ) -> None:
        """Validate that actions don't reference actions in inner scopes."""
        # Pre-compute loop-end closure metadata once so downstream checks can be
        # strict and O(1) per reference.
        loop_end_scope_by_ref: dict[str, str] = {}
        loop_end_condition_refs_by_ref: dict[str, set[str]] = {}
        for action in self.actions:
            if action.action != PlatformAction.LOOP_END:
                continue
            loop_end_scope_by_ref[action.ref] = self._resolve_closed_loop_scope(
                loop_end_stmt=action,
                action_scopes=action_scopes,
                scope_hierarchy=scope_hierarchy,
                scope_openers=scope_openers,
            )
            condition_value = action.args.get("condition")
            condition_expr_ctxs = extract_expressions({"condition": condition_value})
            loop_end_condition_refs_by_ref[action.ref] = set(
                condition_expr_ctxs[ExprContext.ACTIONS]
            )

        for action in self.actions:
            # Logic:
            # Scatter - must depend on an action in a parent scope
            # Gather - must depend on an action in a child scope
            # All other actions - must depend on an action in the same scope
            action_scope = action_scopes[action.ref]

            # Validate edge dependencies
            for dep in action.depends_on:
                dep_ref, _ = edge_components_from_dep(dep)
                dep_scope = action_scopes[dep_ref]
                if action.action in SCOPE_OPENER_ACTIONS:
                    if dep_scope != scope_hierarchy[action_scope]:
                        if action.action == PlatformAction.TRANSFORM_SCATTER:
                            msg = (
                                f"Scatter action '{action.ref}' has an edge from '{dep}',"
                                " which isn't the parent scope"
                            )
                        else:
                            msg = (
                                f"Loop start action '{action.ref}' has an edge from"
                                f" '{dep}', which isn't the parent scope"
                            )
                        raise TracecatDSLError(msg)
                elif action.action == PlatformAction.LOOP_END:
                    # loop_end must only depend on actions from the loop scope
                    # it closes; otherwise it can accidentally close a sibling scope.
                    closed_loop_scope = loop_end_scope_by_ref[action.ref]
                    if dep_scope != closed_loop_scope:
                        raise TracecatDSLError(
                            f"Loop end action '{action.ref}' has an edge from '{dep}', "
                            f"which isn't the closed loop scope '{closed_loop_scope}'"
                        )
                elif action.action in SCOPE_CLOSER_ACTIONS:
                    # Here, action_scope is the parent scope
                    if action_scope != scope_hierarchy[dep_scope]:
                        opener = scope_openers.get(dep_scope)
                        if (
                            action.action in CLOSER_TO_OPENER_ACTION
                            and opener != CLOSER_TO_OPENER_ACTION[action.action]
                        ):
                            raise TracecatDSLError(
                                f"Action '{action.ref}' closes the wrong scope type for edge '{dep}'"
                            )
                        if action.action == PlatformAction.TRANSFORM_GATHER:
                            msg = (
                                f"Gather action '{action.ref}' has an edge from '{dep}',"
                                " which isn't the child scope"
                            )
                        else:
                            msg = (
                                f"Loop end action '{action.ref}' has an edge from"
                                f" '{dep}', which isn't the child scope"
                            )
                        raise TracecatDSLError(msg)
                else:
                    if dep_scope != action_scope:
                        raise TracecatDSLError(
                            f"Action '{action.ref}' has an edge from '{dep}', which is in a different scatter-gather scope"
                        )

            # Validate expression dependencies
            for key, value in action.model_dump(exclude={"depends_on"}).items():
                expr_ctxs = extract_expressions(value)
                dep_refs = expr_ctxs[ExprContext.ACTIONS]
                for dep in dep_refs:
                    dep_scope = action_scopes[dep]
                    if action.action in SCOPE_OPENER_ACTIONS:
                        if dep_scope != scope_hierarchy[action_scope]:
                            if action.action == PlatformAction.TRANSFORM_SCATTER:
                                msg = (
                                    f"Scatter action '{action.ref}' has an expression in field '{key2loc(key)}' "
                                    f"that references '{dep}', which isn't the parent scope"
                                )
                            else:
                                msg = (
                                    f"Loop start action '{action.ref}' has an expression in field '{key2loc(key)}' "
                                    f"that references '{dep}', which isn't the parent scope"
                                )
                            raise TracecatDSLError(msg)
                    elif action.action == PlatformAction.LOOP_END:
                        closed_loop_scope = loop_end_scope_by_ref[action.ref]
                        condition_refs = loop_end_condition_refs_by_ref[action.ref]
                        # Enforce strict scope rules only for loop-end condition refs.
                        # This is the decision point that controls iteration continuation.
                        if key == "args" and dep in condition_refs:
                            if dep_scope != closed_loop_scope:
                                raise TracecatDSLError(
                                    f"Loop end action '{action.ref}' has an expression "
                                    f"in field '{key2loc(key)}.condition' that "
                                    f"references '{dep}', but condition refs must be "
                                    f"in loop scope '{closed_loop_scope}'."
                                )
                        # Keep existing closer semantics for non-condition expression refs.
                        if action_scope != scope_hierarchy[dep_scope]:
                            opener = scope_openers.get(dep_scope)
                            if (
                                action.action in CLOSER_TO_OPENER_ACTION
                                and opener != CLOSER_TO_OPENER_ACTION[action.action]
                            ):
                                raise TracecatDSLError(
                                    f"Action '{action.ref}' closes the wrong scope type for expression dependency '{dep}'"
                                )
                            msg = (
                                f"Loop end action '{action.ref}' has an expression in field '{key2loc(key)}' "
                                f"that references '{dep}', which isn't the child scope"
                            )
                            raise TracecatDSLError(msg)
                    elif action.action in SCOPE_CLOSER_ACTIONS:
                        # Here, action_scope is the parent scope
                        if action_scope != scope_hierarchy[dep_scope]:
                            opener = scope_openers.get(dep_scope)
                            if (
                                action.action in CLOSER_TO_OPENER_ACTION
                                and opener != CLOSER_TO_OPENER_ACTION[action.action]
                            ):
                                raise TracecatDSLError(
                                    f"Action '{action.ref}' closes the wrong scope type for expression dependency '{dep}'"
                                )
                            if action.action == PlatformAction.TRANSFORM_GATHER:
                                msg = (
                                    f"Gather action '{action.ref}' has an expression in field '{key2loc(key)}' "
                                    f"that references '{dep}', which isn't the child scope"
                                )
                            else:
                                msg = (
                                    f"Loop end action '{action.ref}' has an expression in field '{key2loc(key)}' "
                                    f"that references '{dep}', which isn't the child scope"
                                )
                            raise TracecatDSLError(msg)
                    else:
                        # Dep scope must be the same as action_scope or an ancestor (parent, grandparent, etc.)
                        is_ancestor = self._is_same_or_ancestor_scope(
                            dep_scope=dep_scope,
                            action_scope=action_scope,
                            scope_hierarchy=scope_hierarchy,
                        )
                        is_loop_descendant = self._is_loop_descendant_scope(
                            dep_scope=dep_scope,
                            action_scope=action_scope,
                            scope_hierarchy=scope_hierarchy,
                            scope_openers=scope_openers,
                        )
                        if not is_ancestor and not is_loop_descendant:
                            raise TracecatDSLError(
                                f"Action '{action.ref}' has an expression in field '{key2loc(key)}' that references '{dep}' which cannot be referenced from this scope"
                            )

    def _resolve_closed_loop_scope(
        self,
        *,
        loop_end_stmt: ActionStatement,
        action_scopes: dict[str, str],
        scope_hierarchy: dict[str, str | None],
        scope_openers: dict[str, str],
    ) -> str:
        """Resolve and validate the loop scope closed by a `core.loop.end` action.

        A valid `core.loop.end` must:
        - depend on actions from exactly one scope
        - close a direct child scope of its own scope
        - close a scope opened by `core.loop.start`
        """
        action_scope = action_scopes[loop_end_stmt.ref]
        # Convert depends_on refs into scopes and validate the closer targets a
        # single region.
        dep_scopes = {
            action_scopes[edge_components_from_dep(dep_ref)[0]]
            for dep_ref in loop_end_stmt.depends_on
        }
        if len(dep_scopes) != 1:
            raise TracecatDSLError(
                f"Loop end action '{loop_end_stmt.ref}' must depend on actions "
                "from exactly one loop scope"
            )

        loop_scope = next(iter(dep_scopes))
        # A closer in parent scope can only close one of its direct children.
        if scope_hierarchy.get(loop_scope) != action_scope:
            raise TracecatDSLError(
                f"Loop end action '{loop_end_stmt.ref}' must depend on actions "
                "from its child loop scope"
            )
        if scope_openers.get(loop_scope) != PlatformAction.LOOP_START:
            raise TracecatDSLError(
                f"Loop end action '{loop_end_stmt.ref}' must close a loop.start scope"
            )
        return loop_scope

    def _validate_loop_scope_synchronization(
        self,
        *,
        action_scopes: dict[str, str],
        scope_hierarchy: dict[str, str | None],
        scope_openers: dict[str, str],
    ) -> None:
        """Validate that every do-while loop region synchronizes at `core.loop.end`.

        Invariant
        ---------
        For each loop scope opened by `core.loop.start`, every action inside that
        scope (including nested descendant scopes) must have a SUCCESS-path to the
        matching `core.loop.end`.

        Why this exists
        ---------------
        Loop continuation resets in-loop scheduler state and queues the next
        iteration. If any in-loop branch can still execute after loop_end decides
        to continue, iterations can interleave and race on `ACTIONS` writes.
        Enforcing a single synchronization barrier at loop_end prevents that.

        Algorithm
        ---------
        1. Collect loop scopes from `scope_openers`.
        2. Resolve exactly one `loop_end` per loop scope from dependency scopes.
        3. Build a reverse adjacency graph over SUCCESS edges only.
        4. For each loop scope, reverse-traverse from `loop_end` to compute all
           actions that can reach it.
        5. Fail if any action in the loop scope/descendants is missing from this
           reachable set.
        """
        loop_scopes = {
            scope_ref
            for scope_ref, opener in scope_openers.items()
            if opener == PlatformAction.LOOP_START
        }
        if not loop_scopes:
            return

        loop_end_by_scope: dict[str, str] = {}
        for stmt in self.actions:
            if stmt.action != PlatformAction.LOOP_END:
                continue
            loop_scope = self._resolve_closed_loop_scope(
                loop_end_stmt=stmt,
                action_scopes=action_scopes,
                scope_hierarchy=scope_hierarchy,
                scope_openers=scope_openers,
            )
            if existing := loop_end_by_scope.get(loop_scope):
                raise TracecatDSLError(
                    f"Loop start action '{loop_scope}' has multiple loop end actions: "
                    f"'{existing}' and '{stmt.ref}'"
                )
            loop_end_by_scope[loop_scope] = stmt.ref

        missing_end_scopes = sorted(loop_scopes - set(loop_end_by_scope))
        if missing_end_scopes:
            missing_scope = missing_end_scopes[0]
            raise TracecatDSLError(
                f"Loop start action '{missing_scope}' has no matching loop end action"
            )

        reverse_success_adj: dict[str, set[str]] = {
            action.ref: set() for action in self.actions
        }
        for src_ref, outgoing in self._to_typed_adjacency().items():
            for dst_ref, edge_type in outgoing:
                if edge_type == EdgeType.SUCCESS:
                    reverse_success_adj[dst_ref].add(src_ref)

        # We've validated that every loop scope has a loop end action, so we can now
        # check that every action in the loop region can reach the loop end action.
        for loop_scope, loop_end_ref in loop_end_by_scope.items():
            can_reach_loop_end: set[str] = set()
            stack = [loop_end_ref]
            while stack:
                curr_ref = stack.pop()
                if curr_ref in can_reach_loop_end:
                    continue
                can_reach_loop_end.add(curr_ref)
                stack.extend(reverse_success_adj.get(curr_ref, ()))

            unsynchronized_actions = sorted(
                action_ref
                for action_ref, scope in action_scopes.items()
                if self._is_same_or_descendant_scope(
                    scope=scope,
                    ancestor_scope=loop_scope,
                    scope_hierarchy=scope_hierarchy,
                )
                and action_ref not in can_reach_loop_end
            )
            if unsynchronized_actions:
                raise TracecatDSLError(
                    f"Loop scope opened by '{loop_scope}' must synchronize at "
                    f"'{loop_end_ref}'. Every action in the loop region needs a success path "
                    "to loop_end so iterations cannot interleave and overwrite each other. "
                    f"Unsynchronized actions: {unsynchronized_actions}"
                )

    @staticmethod
    def _is_same_or_ancestor_scope(
        dep_scope: str,
        action_scope: str,
        scope_hierarchy: dict[str, str | None],
    ) -> bool:
        curr_scope: str | None = action_scope
        while curr_scope is not None:
            if dep_scope == curr_scope:
                return True
            curr_scope = scope_hierarchy.get(curr_scope)
        return False

    @staticmethod
    def _is_same_or_descendant_scope(
        scope: str,
        ancestor_scope: str,
        scope_hierarchy: dict[str, str | None],
    ) -> bool:
        """Return True if scope equals ancestor_scope or descends from it."""
        curr_scope: str | None = scope
        while curr_scope is not None:
            if curr_scope == ancestor_scope:
                return True
            curr_scope = scope_hierarchy.get(curr_scope)
        return False

    @staticmethod
    def _is_loop_descendant_scope(
        dep_scope: str,
        action_scope: str,
        scope_hierarchy: dict[str, str | None],
        scope_openers: dict[str, str],
    ) -> bool:
        """Return True when dep_scope is a descendant via only loop scopes."""
        if dep_scope == action_scope:
            return False

        curr_scope: str | None = dep_scope
        while curr_scope is not None and curr_scope != action_scope:
            opener = scope_openers.get(curr_scope)
            if opener != PlatformAction.LOOP_START:
                return False
            curr_scope = scope_hierarchy.get(curr_scope)

        return curr_scope == action_scope

    def _assign_action_scopes(
        self, adj: dict[str, list[str]]
    ) -> tuple[dict[str, str], dict[str, str | None], dict[str, str]]:
        """Assign scope IDs to actions using topological sort.

        Returns a mapping of action ref -> scope ID.
        Raises TracecatDSLError if an action is assigned to multiple scopes.
        """

        stmts = {a.ref: a for a in self.actions}
        scopes: dict[str, str] = {}
        scope_openers: dict[str, str] = {}

        ROOT_SCOPE = "<root>"
        scope_hierarchy: dict[str, str | None] = {ROOT_SCOPE: None}

        # Build indegrees for topological sort
        indegrees: dict[str, int] = {}
        for action in self.actions:
            indegrees[action.ref] = len(action.depends_on)

        # Queue for topological sort
        queue = deque[tuple[str, str]]()

        # Add all actions with no dependencies to queue
        for ref, indegree in indegrees.items():
            if indegree == 0:
                queue.append((ref, ROOT_SCOPE))

        # Process actions in topological order
        def assign_scope(action_ref: str, scope: str) -> None:
            """Assign a scope to an action."""
            if action_ref not in scopes:
                scopes[action_ref] = scope
            else:
                if scopes[action_ref] != scope:
                    raise TracecatDSLError(
                        f"Action {action_ref!r} cannot belong to multiple scopes: "
                        f"already in {scopes[action_ref]!r}, trying to assign to {scope!r}"
                    )

        n_visited = 0
        while queue:
            ref, curr_scope = queue.popleft()
            n_visited += 1

            # Check for conflict. If the action hasn't been assigned a scope, assign it.
            # Otherwise, if we somehow end up in a different scope, raise an error.
            # Handle scope transitions
            stmt = stmts[ref]
            if stmt.action in SCOPE_OPENER_ACTIONS:
                # Opener actions create a new scope
                next_scope = ref
                assign_scope(ref, next_scope)
                scope_hierarchy[next_scope] = curr_scope
                scope_openers[next_scope] = stmt.action
            elif stmt.action in SCOPE_CLOSER_ACTIONS:
                # Closer actions close the current scope
                next_scope = scope_hierarchy.get(curr_scope)
                if next_scope is None:
                    action_name = (
                        "gather"
                        if stmt.action == PlatformAction.TRANSFORM_GATHER
                        else "loop.end"
                    )
                    raise TracecatDSLError(
                        f"You cannot use a {action_name} action {ref!r} in the root scope"
                    )
                expected_opener = CLOSER_TO_OPENER_ACTION[stmt.action]
                if scope_openers.get(curr_scope) != expected_opener:
                    raise TracecatDSLError(
                        f"Action {ref!r} closes the wrong scope type"
                    )
                assign_scope(ref, next_scope)
            else:
                # Everything else is a regular action
                assign_scope(ref, curr_scope)
                next_scope = curr_scope

            # Update indegrees and queue next actions
            for next_ref in adj.get(ref, []):
                indegrees[next_ref] -= 1
                if indegrees[next_ref] == 0:
                    queue.append((next_ref, next_scope))

        # Check if we have cycles
        if n_visited != len(self.actions):
            raise TracecatDSLError("Cycle detected in control-flow workflow")

        return scopes, scope_hierarchy, scope_openers

    def _to_adjacency(self) -> dict[str, list[str]]:
        """Convert the DSLInput to an adjacency list."""
        adj: dict[str, list[str]] = {}
        for action in self.actions:
            adj[action.ref] = []
        for action in self.actions:
            for dep in action.depends_on:
                src_ref, _ = edge_components_from_dep(dep)
                adj[src_ref].append(action.ref)
        return adj

    def _to_typed_adjacency(self) -> dict[str, list[tuple[str, EdgeType]]]:
        """Convert the DSLInput to adjacency with edge types."""
        adj: dict[str, list[tuple[str, EdgeType]]] = {}
        for action in self.actions:
            adj[action.ref] = []
        for action in self.actions:
            for dep in action.depends_on:
                src_ref, edge_type = edge_components_from_dep(dep)
                adj[src_ref].append((action.ref, edge_type))
        return adj

    @staticmethod
    def from_yaml(path: str | Path | SpooledTemporaryFile) -> DSLInput:
        """Read a DSL definition from a YAML file."""
        # Handle binaryIO
        if isinstance(path, str | Path):
            with Path(path).open("r") as f:
                yaml_str = f.read()
        elif isinstance(path, SpooledTemporaryFile):
            yaml_str = path.read().decode()
        else:
            raise TracecatDSLError(f"Invalid file/path type {type(path)}")
        dsl_dict = yaml.safe_load(yaml_str)
        try:
            return DSLInput.model_validate(dsl_dict)
        except* TracecatDSLError as eg:
            logger.error(eg.message, error=eg.exceptions)
            raise eg

    def to_yaml(self, path: str | Path) -> None:
        with Path(path).expanduser().resolve().open("w") as f:
            yaml.dump(self.model_dump(), f)

    def dump_yaml(self) -> str:
        return yaml.dump(self.model_dump())

    def to_graph(
        self, trigger_node: TriggerNode, ref2id: dict[str, ActionID]
    ) -> RFGraph:
        """Construct a new react flow graph from this DSLInput.

        We depend on the trigger from the old graph to create the new graph.

        Args:
            trigger_node: The trigger node from the workflow
            ref2id: Mapping from action ref (slugified title) to action ID (UUID)
        """

        # Create nodes and edges
        nodes: list[NodeVariant] = [trigger_node]
        edges: list[RFEdge] = []
        try:
            for action in self.actions:
                # Get updated nodes
                dst_id = ref2id[action.ref]
                node = UDFNode(
                    id=dst_id,
                    data=UDFNodeData(
                        type=action.action,
                    ),
                )
                nodes.append(node)

                if not action.depends_on:
                    # If there are no dependencies, this is an entrypoint
                    entrypoint_id = ref2id[action.ref]
                    edges.append(
                        RFEdge(
                            source=trigger_node.id,
                            target=entrypoint_id,
                            label="⚡ Trigger",
                        )
                    )
                else:
                    # Otherwise, add edges for all dependencies
                    for dep_ref in action.depends_on:
                        src_ref, edge_type = edge_components_from_dep(dep_ref)
                        src_id = ref2id[src_ref]
                        edges.append(
                            RFEdge(
                                source=src_id, target=dst_id, source_handle=edge_type
                            )
                        )

            return RFGraph(nodes=nodes, edges=edges)
        except Exception as e:
            logger.opt(exception=e).error("Error creating graph")
            raise e


class SubflowContext(BaseModel):
    """Shared context for child workflow execution (prepared once).

    Contains workflow definition and execution context that is shared across
    all iterations in a loop. Per-iteration config (environment, timeout) is
    resolved separately via ResolvedSubflowBatch.
    """

    wf_id: WorkflowUUID
    dsl: DSLInput
    registry_lock: RegistryLock | None = None
    run_context: RunContext
    execution_type: ExecutionType
    time_anchor: datetime
    batch_size: int


class DSLRunArgs(BaseModel):
    role: Role
    dsl: DSLInput | None = None
    wf_id: WorkflowUUID
    trigger_inputs: StoredObject | None = None
    parent_run_context: RunContext | None = None
    runtime_config: DSLConfig = Field(
        default_factory=DSLConfig,
        description=(
            "Runtime configuration that can be set on workflow entry. "
            "Note that this can override the default config in DSLInput."
        ),
    )
    timeout: timedelta = Field(
        default_factory=lambda: timedelta(minutes=5),
        description="Platform activity start-to-close timeout.",
    )
    """Platform activity start-to-close timeout."""
    schedule_id: ScheduleUUID | None = Field(
        default=None,
        description="The schedule ID that triggered this workflow, if any. Auto-converts from legacy 'sch-<hex>' format.",
    )
    execution_type: ExecutionType = Field(
        default=ExecutionType.PUBLISHED,
        description="Execution type (draft or published). Draft executions use draft aliases for child workflows.",
    )
    time_anchor: datetime | None = Field(
        default=None,
        description=(
            "The workflow's logical time anchor for FN.now() and related functions. "
            "If not provided, computed from TemporalScheduledStartTime (for schedules) "
            "or workflow start_time (for other triggers). Stored as UTC."
        ),
    )
    registry_lock: RegistryLock | None = Field(
        default=None,
        description="Registry version lock for action execution. Contains origins (origin -> version) and actions (action_name -> origin) mappings.",
    )

    @field_validator("wf_id", mode="before")
    @classmethod
    def validate_workflow_id(cls, v: AnyWorkflowID) -> WorkflowUUID:
        """Convert any valid workflow ID format to WorkflowUUID."""
        return WorkflowUUID.new(v)


class _BaseSubflowArgs(BaseModel):
    workflow_id: WorkflowUUID | None = None
    workflow_alias: str | None = None
    environment: str | None = None
    version: int | None = None
    loop_strategy: LoopStrategy = LoopStrategy.BATCH
    batch_size: int = 32
    fail_strategy: FailStrategy = FailStrategy.ISOLATED
    timeout: float | None = None
    wait_strategy: WaitStrategy = WaitStrategy.WAIT
    time_anchor: datetime | None = Field(
        default=None,
        description="Override time anchor for subflow. If None, inherits from parent.",
    )

    @model_validator(mode="after")
    def validate_workflow_id_or_alias(self) -> Self:
        if self.workflow_id is None and self.workflow_alias is None:
            # This class enables proper serialization of the error
            raise PydanticCustomError(
                "value_error.missing_workflow_identifier",
                "Either workflow_id or workflow_alias must be provided",
                {
                    "workflow_id": self.workflow_id,
                    "workflow_alias": self.workflow_alias,
                    "loc": ["workflow_id", "workflow_alias"],
                },
            )
        return self

    @field_validator("workflow_id", mode="before")
    @classmethod
    def validate_workflow_id(cls, v: AnyWorkflowID) -> WorkflowUUID:
        """Convert any valid workflow ID format to WorkflowUUID."""
        return WorkflowUUID.new(v)


class ExecuteSubflowArgs(_BaseSubflowArgs):
    """Action arguments for executing a subflow. Use to validate user-provided subflow arguments."""

    trigger_inputs: Any | None = None
    """The unresolved trigger inputs for the subflow."""


class ResolvedSubflowInput(_BaseSubflowArgs):
    """Input for executing a subflow."""

    trigger_inputs: StoredObject | None = None
    ref_index: int | None = None


class ResolvedSubflowConfig(BaseModel):
    """Per-iteration DSL config for subflow execution.

    Contains only the DSL primitives that can be overridden per iteration
    via var expressions (e.g., environment: ${{ var.item.env }}).
    """

    environment: str | None = None
    timeout: float | None = None


class ResolvedSubflowBatch(BaseModel):
    """Per-batch resolved args for looped subflow execution.

    Separates DSL config (small, can be inlined) from trigger_inputs
    (each stored as individual StoredObject for direct passing to child workflows).

    The configs field is optimized: if all iterations share the same config
    (no var expressions in DSL primitives), a single config is returned.
    Otherwise, a list matching trigger_inputs length is returned.
    """

    configs: ResolvedSubflowConfig | list[ResolvedSubflowConfig]
    trigger_inputs: list[StoredObject]
    """Each item is the trigger_inputs for one iteration, stored as StoredObject."""

    @model_validator(mode="after")
    def validate_configs_length(self) -> Self:
        if isinstance(self.configs, list):
            if len(self.configs) != len(self.trigger_inputs):
                raise ValueError(
                    f"configs length ({len(self.configs)}) must match "
                    f"trigger_inputs length ({len(self.trigger_inputs)})"
                )
        return self

    def get_config(self, index: int) -> ResolvedSubflowConfig:
        """Get config for iteration, handling shared vs per-iteration."""
        if isinstance(self.configs, list):
            return self.configs[index]
        return self.configs

    @property
    def count(self) -> int:
        """Number of iterations in this batch."""
        return len(self.trigger_inputs)


MAX_LOOP_ITERATIONS = 4096


class PreparedSubflowResult(BaseModel):
    """Result of prepare_subflow_activity containing all data needed to spawn child workflows.

    For single subflows: trigger_inputs/runtime_configs are None, evaluate separately.
    For looped subflows: trigger_inputs is stored collection, runtime_configs uses T|list[T].
    """

    wf_id: WorkflowUUID
    """Resolved workflow ID (from alias or direct)."""

    dsl: DSLInput
    """Workflow definition."""

    registry_lock: RegistryLock | None = None
    """Frozen dependency versions. May be None for workflows without locks."""

    trigger_inputs: StoredObject | None = None
    """For loops: CollectionObject or InlineObject containing trigger_inputs list."""

    runtime_configs: DSLConfig | list[DSLConfig] | None = None
    """For loops: T|list[T] optimized configs. None for single subflow."""

    @property
    def count(self) -> int:
        """Number of iterations (1 for single subflow, N for loops)."""
        match self.trigger_inputs:
            case None:
                return 1
            case CollectionObject() as col:
                return col.count
            case InlineObject(data=data) if isinstance(data, list):
                return len(data)
            case _:
                raise TypeError(
                    f"Expected CollectionObject or InlineObject with list, "
                    f"got {type(self.trigger_inputs).__name__}"
                )

    def get_trigger_input_at(self, index: int) -> StoredObject | None:
        """Get trigger_inputs for a specific iteration.

        For CollectionObject: returns a handle pointing to the indexed item.
        For InlineObject: extracts the item and wraps in InlineObject.
        """
        match self.trigger_inputs:
            case None:
                return None
            case CollectionObject() as col:
                return col.at(index)
            case InlineObject(data=data) if isinstance(data, list):
                return InlineObject(data=data[index])
            case _:
                raise TypeError(
                    f"Expected CollectionObject or InlineObject with list, "
                    f"got {type(self.trigger_inputs).__name__}"
                )

    def get_config(self, index: int) -> DSLConfig:
        """Get runtime config for iteration, handling T|list[T] optimization."""
        if self.runtime_configs is None:
            return self.dsl.config
        if isinstance(self.runtime_configs, list):
            return self.runtime_configs[index]
        return self.runtime_configs


class AgentActionMemo(BaseModel):
    action_ref: str = Field(
        ..., description="The action ref that initiated the child workflow."
    )
    action_title: str | None = Field(
        default=None, description="The action title that initiated the child workflow."
    )
    loop_index: int | None = Field(
        default=None,
        description="The loop index of the child workflow, if any.",
    )
    stream_id: StreamID = Field(
        default=ROOT_STREAM,
        description="The execution stream ID where the agent workflow was spawned.",
    )

    @classmethod
    def from_temporal(cls, memo: temporalio.api.common.v1.Memo) -> AgentActionMemo:
        data: dict[str, Any] = {}
        for key, value in memo.fields.items():
            try:
                data[key] = _memo_payload_converter.from_payload(value)
            except Exception as e:
                logger.warning(
                    "Error parsing agent action memo field",
                    error=e,
                    key=key,
                    value=value,
                )
        if not data.get("action_ref"):
            data["action_ref"] = "unknown_agent_action"
        if not data.get("stream_id"):
            data["stream_id"] = ROOT_STREAM
        return cls(**data)


class ChildWorkflowMemo(BaseModel):
    action_ref: str = Field(
        ..., description="The action ref that initiated the child workflow."
    )
    loop_index: int | None = Field(
        default=None,
        description="The loop index of the child workflow, if any.",
    )
    wait_strategy: WaitStrategy = Field(
        default=WaitStrategy.WAIT,
        description="The wait strategy of the child workflow.",
    )
    stream_id: StreamID = Field(
        default=ROOT_STREAM,
        description="The stream ID of the child workflow.",
    )

    @staticmethod
    def from_temporal(memo: temporalio.api.common.v1.Memo) -> ChildWorkflowMemo:
        try:
            action_ref = orjson.loads(memo.fields["action_ref"].data)
        except Exception as e:
            logger.warning("Error parsing child workflow memo action ref", error=e)
            action_ref = "Unknown Child Workflow"
        if loop_index_data := memo.fields["loop_index"].data:
            loop_index = orjson.loads(loop_index_data)
        else:
            loop_index = None
        try:
            wait_strategy = WaitStrategy(
                orjson.loads(memo.fields["wait_strategy"].data)
            )
        except Exception as e:
            logger.warning("Error parsing child workflow memo wait strategy", error=e)
            wait_strategy = WaitStrategy.WAIT
        try:
            stream_id = StreamID(orjson.loads(memo.fields["stream_id"].data))
        except Exception as e:
            logger.warning("Error parsing child workflow memo stream id", error=e)
            stream_id = ROOT_STREAM
        return ChildWorkflowMemo(
            action_ref=action_ref,
            loop_index=loop_index,
            wait_strategy=wait_strategy,
            stream_id=stream_id,
        )


AdjDst = tuple[str, EdgeType]


def edge_components_from_dep(dep_ref: str) -> AdjDst:
    src_ref, *path = dep_ref.split(".", 1)
    if not path or path[0] == EdgeType.SUCCESS:
        return src_ref, EdgeType.SUCCESS
    elif path[0] == EdgeType.ERROR:
        return src_ref, EdgeType.ERROR
    raise ValueError(f"Invalid edge type: {path[0]} in {dep_ref!r}")


def dep_from_edge_components(src_ref: str, edge_type: EdgeType) -> str:
    return f"{src_ref}.{edge_type.value}"


def context_locator(
    stmt: ActionStatement, loc: str, *, ctx: ExprContext = ExprContext.ACTIONS
) -> str:
    return f"{ctx}.{stmt.ref} -> {loc}"


def build_action_statements_from_actions(
    actions: list[Action],
) -> list[ActionStatement]:
    """Convert DB Actions into ActionStatements using upstream_edges.

    This function uses Action.upstream_edges directly as the source of truth
    for dependencies, eliminating the need for a separate RFGraph object.

    Only actions reachable from trigger-connected roots are converted.
    This prevents disconnected action islands from being executable.

    Legacy fallback: if a workflow has no trigger edges at all, keep previous
    behavior and include all actions.
    """

    def get_reachable_action_ids(actions: list[Action]) -> set[ActionID]:
        id2action = {action.id: action for action in actions}
        trigger_roots: set[ActionID] = set()
        adjacency: dict[ActionID, set[ActionID]] = {}

        for action in actions:
            for edge_data in action.upstream_edges:
                edge = UpstreamEdgeDataValidator.validate_python(edge_data)
                source_type = edge.get("source_type")
                source_id_str = edge.get("source_id")
                if not source_id_str:
                    continue

                # Legacy edge format omitted source_type for trigger roots.
                if source_type == "trigger" or (
                    source_type is None and source_id_str.startswith("trigger-")
                ):
                    trigger_roots.add(action.id)
                    continue
                if source_type is None:
                    source_type = "udf"
                if source_type != "udf":
                    continue

                try:
                    source_id = ActionID(source_id_str)
                except ValueError:
                    continue

                if source_id in id2action:
                    adjacency.setdefault(source_id, set()).add(action.id)

        if not trigger_roots:
            return set(id2action)

        reachable: set[ActionID] = set(trigger_roots)
        queue = deque(trigger_roots)
        while queue:
            source_id = queue.popleft()
            for target_id in adjacency.get(source_id, set()):
                if target_id in reachable:
                    continue
                reachable.add(target_id)
                queue.append(target_id)
        return reachable

    id2action = {action.id: action for action in actions}
    reachable_action_ids = get_reachable_action_ids(actions)

    statements = []
    for action in actions:
        if action.id not in reachable_action_ids:
            continue

        dependencies: list[str] = []

        # Build dependencies from upstream_edges
        for edge_data in action.upstream_edges:
            # Validate edge data at runtime using TypeAdapter
            edge = UpstreamEdgeDataValidator.validate_python(edge_data)
            source_id_str = edge.get("source_id")
            source_handle = edge.get("source_handle", "success")

            # Convert string source_id to ActionID (UUID) for lookup
            if source_id_str:
                try:
                    source_id = ActionID(source_id_str)
                except ValueError:
                    continue  # Skip invalid UUIDs
            else:
                continue

            if source_id in id2action and source_id in reachable_action_ids:
                source_action = id2action[source_id]
                base_ref = source_action.ref

                if source_handle == "error":
                    ref = dep_from_edge_components(base_ref, EdgeType.ERROR)
                else:
                    ref = base_ref
                dependencies.append(ref)

        dependencies = sorted(dependencies)

        control_flow = ActionControlFlow.model_validate(action.control_flow)
        args = yaml.safe_load(action.inputs) or {}
        interaction = (
            ActionInteractionValidator.validate_python(action.interaction)
            if action.is_interactive and action.interaction
            else None
        )
        action_stmt = ActionStatement(
            id=action.id,
            ref=action.ref,
            action=action.type,
            args=args,
            depends_on=dependencies,
            run_if=control_flow.run_if,
            for_each=control_flow.for_each,
            retry_policy=control_flow.retry_policy,
            start_delay=control_flow.start_delay,
            wait_until=control_flow.wait_until,
            join_strategy=control_flow.join_strategy,
            interaction=interaction,
            environment=control_flow.environment,
        )
        statements.append(action_stmt)
    return statements


def create_default_execution_context(
    ACTIONS: dict[str, TaskResult] | None = None,
    TRIGGER: StoredObject | None = None,
    ENV: DSLEnvironment | None = None,
    VARS: dict[str, Any] | None = None,
) -> ExecutionContext:
    ctx = ExecutionContext(
        ACTIONS=ACTIONS or {},
        TRIGGER=TRIGGER,
        ENV=ENV or DSLEnvironment(),
    )
    if VARS:
        ctx["VARS"] = VARS
    return ctx


def dsl_execution_error_from_exception(e: BaseException) -> DSLExecutionError:
    return DSLExecutionError(
        is_error=True,
        type=e.__class__.__name__,
        message=str(e),
    )


def get_trigger_type(info: workflow.Info) -> TriggerType:
    search_attributes = info.typed_search_attributes
    return get_trigger_type_from_search_attr(search_attributes, info.workflow_id)


def get_trigger_type_from_search_attr(
    search_attributes: TypedSearchAttributes, temporal_workflow_id: str
) -> TriggerType:
    trigger_type = search_attributes.get(TemporalSearchAttr.TRIGGER_TYPE.key)
    if trigger_type is None:
        logger.debug(
            "Couldn't find trigger type, using manual as fallback",
            workflow_id=temporal_workflow_id,
        )
        return TriggerType.MANUAL
    return TriggerType(trigger_type)


def get_execution_type_from_search_attr(
    search_attributes: TypedSearchAttributes,
) -> ExecutionType:
    """Extract execution type from search attributes."""
    execution_type = search_attributes.get(TemporalSearchAttr.EXECUTION_TYPE.key)
    if execution_type is None:
        # Default to published for historical executions without the attribute
        return ExecutionType.PUBLISHED
    return ExecutionType(execution_type)


NON_RETRYABLE_ERROR_TYPES = [
    # General
    Exception.__name__,
    TypeError.__name__,
    ValueError.__name__,
    RuntimeError.__name__,
    # Pydantic
    ValidationError.__name__,
    # Tracecat
    TracecatException.__name__,
    TracecatExpressionError.__name__,
    TracecatValidationError.__name__,
    TracecatDSLError.__name__,
    TracecatCredentialsError.__name__,
    # Temporal
    ApplicationError.__name__,
    ChildWorkflowError.__name__,
    FailureError.__name__,
]

RETRY_POLICIES = {
    "activity:fail_fast": RetryPolicy(
        # XXX: Do not set max attempts to 0, it will default to unlimited
        maximum_attempts=1,
        non_retryable_error_types=NON_RETRYABLE_ERROR_TYPES,
    ),
    "activity:fail_slow": RetryPolicy(maximum_attempts=6),
    "workflow:fail_fast": RetryPolicy(
        # XXX: Do not set max attempts to 0, it will default to unlimited
        maximum_attempts=1,
        non_retryable_error_types=NON_RETRYABLE_ERROR_TYPES,
    ),
}
