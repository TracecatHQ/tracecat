"""Tracecat DSL Common Module."""

from __future__ import annotations

from collections import deque
from datetime import timedelta
from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import Any, Self, cast

import orjson
import temporalio.api.common.v1
import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError
from temporalio import workflow
from temporalio.common import SearchAttributeKey, TypedSearchAttributes

from tracecat.db.schemas import Action
from tracecat.dsl.enums import (
    EdgeType,
    FailStrategy,
    LoopStrategy,
    PlatformAction,
    WaitStrategy,
)
from tracecat.dsl.models import (
    ActionStatement,
    DSLConfig,
    DSLEnvironment,
    DSLExecutionError,
    ExecutionContext,
    RunContext,
    Trigger,
    TriggerInputs,
)
from tracecat.dsl.view import RFEdge, RFGraph, RFNode, TriggerNode, UDFNode, UDFNodeData
from tracecat.ee.interactions.models import ActionInteractionValidator
from tracecat.expressions import patterns
from tracecat.expressions.common import ExprContext
from tracecat.expressions.core import extract_expressions
from tracecat.expressions.expectations import ExpectedField
from tracecat.identifiers import ScheduleID
from tracecat.identifiers.workflow import AnyWorkflowID, WorkflowUUID
from tracecat.logger import logger
from tracecat.parse import traverse_leaves
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatDSLError
from tracecat.workflow.actions.models import ActionControlFlow
from tracecat.workflow.executions.enums import TemporalSearchAttr, TriggerType


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


class DSLInput(BaseModel):
    """DSL definition for a workflow.

    The difference between this and a normal workflow engine is that here,
    our workflow execution order is defined by the DSL itself, independent
    of a workflow scheduler.

    With a traditional
    This allows the execution of the workflow to be fully deterministic.
    """

    # Using this for backwards compatibility of existing workflow definitions
    model_config: ConfigDict = ConfigDict(extra="ignore")
    title: str
    description: str
    entrypoint: DSLEntrypoint
    actions: list[ActionStatement]
    config: DSLConfig = Field(default_factory=DSLConfig)
    triggers: list[Trigger] = Field(default_factory=list)
    inputs: dict[str, Any] = Field(
        default_factory=dict, description="Static input parameters"
    )
    returns: Any | None = Field(
        default=None, description="The action ref or value to return."
    )
    error_handler: str | None = Field(
        default=None, description="The action ref to handle errors."
    )

    @field_validator("inputs")
    @classmethod
    def inputs_cannot_have_expressions(cls, inputs: Any) -> dict[str, Any]:
        try:
            exceptions = []
            for loc, value in traverse_leaves(inputs):
                if not isinstance(value, str):
                    continue
                for match in patterns.TEMPLATE_STRING.finditer(value):
                    template = match.group("template")
                    exceptions.append(
                        TracecatDSLError(
                            "Static `INPUTS` context cannot contain expressions,"
                            f" but found {template!r} in INPUTS.{loc}"
                        )
                    )
            if exceptions:
                raise ExceptionGroup("Static `INPUTS` validation failed", exceptions)
            return inputs
        except* TracecatDSLError as eg:
            raise eg

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
        valid_actions = {a.ref for a in self.actions}
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
        invalid_deps = dependencies - valid_actions
        if invalid_deps:
            raise TracecatDSLError(
                f"Invalid depends_on refs in actions: {invalid_deps}."
                f" Valid actions: {valid_actions}"
            )

        self._validate_scatter_gather_scopes()
        return self

    def _validate_scatter_gather_scopes(self) -> None:
        """Validate scatter-gather scope boundaries.

        Logic
        -----
        - We need to map all actions to a scope.
        - Traverse the graph and map out the scopes
        - Outer scope ACTIONS cannot reference inner scope ACTIONS
        - We need to validate that no actions outside the scope reference actions inside the scope.
        - We need to validate that no actions outside the scope reference actions inside the scope.
        """
        # Find scatter and gather actions
        scatter_actions = []
        gather_actions = []
        for action in self.actions:
            if action.action == PlatformAction.TRANSFORM_SCATTER:
                scatter_actions.append(action.ref)
            elif action.action == PlatformAction.TRANSFORM_GATHER:
                gather_actions.append(action.ref)

        if len(gather_actions) > len(scatter_actions):
            raise TracecatDSLError(
                "There are more gather actions than scatter actions. This is not allowed."
            )

        if not scatter_actions:
            return  # No scatter actions, no scope validation needed

        # Build adjacency list for graph traversal
        adj = self._to_adjacency()

        # Assign scope IDs to all actions
        scopes, scope_hierarchy = self._assign_action_scopes(adj)
        self._validate_scope_dependencies(scopes, scope_hierarchy)

    def _check_action_dep_scope(
        self,
        action: ActionStatement,
        action_scope: str,
        dep: str,
        dep_scope: str,
        scope_hierarchy: dict[str, str | None],
    ) -> None:
        if action.action == PlatformAction.TRANSFORM_SCATTER:
            if dep_scope != scope_hierarchy[action_scope]:
                raise TracecatDSLError(
                    f"Scatter action {action.ref!r} depends on {dep!r}, which isn't the parent scope"
                )
        elif action.action == PlatformAction.TRANSFORM_GATHER:
            # Here, action_scope is the parent scope
            if action_scope != scope_hierarchy[dep_scope]:
                raise TracecatDSLError(
                    f"Gather action {action.ref!r} depends on {dep!r}, which isn't the child scope"
                )
        else:
            if dep_scope != action_scope:
                raise TracecatDSLError(
                    f"Action '{action.ref}' depends on '{dep}', which is in a different scope"
                )

    def _validate_scope_dependencies(
        self, action_scopes: dict[str, str], scope_hierarchy: dict[str, str | None]
    ) -> None:
        """Validate that actions don't reference actions in inner scopes."""
        for action in self.actions:
            # Logic:
            # Scatter - must depend on an action in a parent scope
            # Gather - must depend on an action in a child scope
            # All other actions - must depend on an action in the same scope
            action_scope = action_scopes[action.ref]

            # Validate edge dependencies
            for dep in action.depends_on:
                dep_ref, _ = edge_components_from_dep(dep)
                self._check_action_dep_scope(
                    action=action,
                    action_scope=action_scope,
                    dep=dep,
                    dep_scope=action_scopes[dep_ref],
                    scope_hierarchy=scope_hierarchy,
                )
            # Validate expression dependencies
            expr_ctxs = extract_expressions(action.model_dump())
            dep_refs = expr_ctxs[ExprContext.ACTIONS]
            for dep_ref in dep_refs:
                self._check_action_dep_scope(
                    action=action,
                    action_scope=action_scope,
                    dep=dep_ref,
                    dep_scope=action_scopes[dep_ref],
                    scope_hierarchy=scope_hierarchy,
                )

    def _assign_action_scopes(
        self, adj: dict[str, list[str]]
    ) -> tuple[dict[str, str], dict[str, str | None]]:
        """Assign scope IDs to actions using topological sort.

        Returns a mapping of action ref -> scope ID.
        Raises TracecatDSLError if an action is assigned to multiple scopes.
        """

        stmts = {a.ref: a for a in self.actions}
        scopes: dict[str, str] = {}

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
            if stmt.action == PlatformAction.TRANSFORM_SCATTER:
                # Scatter actions create a new scope
                next_scope = ref
                assign_scope(ref, next_scope)
                scope_hierarchy[next_scope] = curr_scope
            elif stmt.action == PlatformAction.TRANSFORM_GATHER:
                # Gather actions close the current scope
                next_scope = scope_hierarchy[curr_scope]
                if next_scope is None:
                    raise TracecatDSLError(
                        f"Gather action {ref!r} in the root scope. Don't allow this"
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
            raise TracecatDSLError("Cycle detected in scatter-gather workflow")

        return scopes, scope_hierarchy

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

    def to_graph(self, trigger_node: TriggerNode, ref2id: dict[str, str]) -> RFGraph:
        """Construct a new react flow graph from this DSLInput.

        We depend on the trigger from the old graph to create the new graph.
        """

        # Create nodes and edges
        nodes: list[RFNode] = [trigger_node]
        edges: list[RFEdge] = []
        try:
            for action in self.actions:
                # Get updated nodes
                dst_id = ref2id[action.ref]
                node = UDFNode(
                    id=dst_id,
                    data=UDFNodeData(
                        title=action.title,
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


class DSLRunArgs(BaseModel):
    role: Role
    dsl: DSLInput | None = None
    wf_id: WorkflowUUID
    trigger_inputs: TriggerInputs | None = None
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
        description="The maximum time to wait for the workflow to complete.",
    )
    schedule_id: ScheduleID | None = Field(
        default=None,
        description="The schedule ID that triggered this workflow, if any.",
    )

    @field_validator("wf_id", mode="before")
    @classmethod
    def validate_workflow_id(cls, v: AnyWorkflowID) -> WorkflowUUID:
        """Convert any valid workflow ID format to WorkflowUUID."""
        return WorkflowUUID.new(v)


class ExecuteChildWorkflowArgs(BaseModel):
    workflow_id: WorkflowUUID | None = None
    workflow_alias: str | None = None
    trigger_inputs: TriggerInputs | None = None
    environment: str | None = None
    version: int | None = None
    loop_strategy: LoopStrategy = LoopStrategy.BATCH
    batch_size: int = 32
    fail_strategy: FailStrategy = FailStrategy.ISOLATED
    timeout: float | None = None
    wait_strategy: WaitStrategy = WaitStrategy.WAIT

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
        return ChildWorkflowMemo(
            action_ref=action_ref, loop_index=loop_index, wait_strategy=wait_strategy
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


def build_action_statements(
    graph: RFGraph, actions: list[Action]
) -> list[ActionStatement]:
    """Convert DB Actions into ActionStatements using the graph."""
    id2action = {action.id: action for action in actions}

    statements = []
    for node in graph.action_nodes():
        dependencies: list[str] = []
        for dep_act_id in graph.dep_list[node.id]:
            base_ref = id2action[dep_act_id].ref
            for edge in graph.edges:
                if edge.source != dep_act_id or edge.target != node.id:
                    continue
                if edge.source_handle == EdgeType.ERROR:
                    ref = dep_from_edge_components(base_ref, edge.source_handle)
                else:
                    ref = base_ref
                dependencies.append(ref)
        dependencies = sorted(dependencies)

        action = id2action[node.id]
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
        )
        statements.append(action_stmt)
    return statements


def create_default_execution_context(
    INPUTS: dict[str, Any] | None = None,
    ACTIONS: dict[str, Any] | None = None,
    TRIGGER: dict[str, Any] | None = None,
    ENV: DSLEnvironment | None = None,
) -> ExecutionContext:
    return {
        ExprContext.INPUTS: INPUTS or {},
        ExprContext.ACTIONS: ACTIONS or {},
        ExprContext.TRIGGER: TRIGGER or {},
        ExprContext.ENV: cast(DSLEnvironment, ENV or {}),
    }


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
    trigger_type = search_attributes.get(
        SearchAttributeKey.for_keyword(TemporalSearchAttr.TRIGGER_TYPE.value)
    )
    if trigger_type is None:
        logger.debug(
            "Couldn't find trigger type, using manual as fallback",
            workflow_id=temporal_workflow_id,
        )
        return TriggerType.MANUAL
    return TriggerType(trigger_type)
