"""Tracecat DSL Common Module."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import Any, Self, TypedDict, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tracecat.contexts import RunContext
from tracecat.db.schemas import Action
from tracecat.dsl.enums import EdgeType, FailStrategy, LoopStrategy
from tracecat.dsl.models import (
    ActionStatement,
    DSLConfig,
    DSLContext,
    DSLEnvironment,
    DSLExecutionError,
    Trigger,
    TriggerInputs,
)
from tracecat.dsl.view import RFEdge, RFGraph, RFNode, TriggerNode, UDFNode, UDFNodeData
from tracecat.expressions import patterns
from tracecat.expressions.expectations import ExpectedField
from tracecat.expressions.shared import ExprContext
from tracecat.identifiers import ScheduleID, WorkflowID
from tracecat.logger import logger
from tracecat.parse import traverse_leaves
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatDSLError
from tracecat.workflow.actions.models import ActionControlFlow


class DSLEntrypoint(BaseModel):
    ref: str | None = Field(default=None, description="The entrypoint action ref")
    expects: dict[str, ExpectedField] | None = Field(
        None,
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
        return self

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

                for src_ref in action.depends_on:
                    src_id = ref2id[src_ref]
                    edges.append(RFEdge(source=src_id, target=dst_id))

            entrypoint_id = ref2id[self.entrypoint.ref]
            # Add trigger edge
            edges.append(
                RFEdge(source=trigger_node.id, target=entrypoint_id, label="⚡ Trigger")
            )
            return RFGraph(nodes=nodes, edges=edges)
        except Exception as e:
            logger.opt(exception=e).error("Error creating graph")
            raise e


class DSLRunArgs(BaseModel):
    role: Role
    dsl: DSLInput | None = None
    wf_id: WorkflowID
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


class ExecuteChildWorkflowArgs(TypedDict):
    workflow_id: WorkflowID
    trigger_inputs: TriggerInputs
    environment: str | None
    version: int | None
    loop_strategy: LoopStrategy | None
    batch_size: int | None
    fail_strategy: FailStrategy | None


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


@dataclass(frozen=True)
class DSLEdge:
    src: str
    dst: str
    type: EdgeType

    def __repr__(self) -> str:
        return f"{self.src}-[{self.type.value}]->{self.dst}"


def context_locator(
    stmt: ActionStatement, loc: str, *, ctx: ExprContext = ExprContext.ACTIONS
) -> str:
    return f"{ctx}.{stmt.ref} -> {loc}"


def build_action_statements(
    graph: RFGraph, actions: list[Action]
) -> list[ActionStatement]:
    """Convert DB Actions into ActionStatements using the graph."""
    ref2action = {action.ref: action for action in actions}

    statements = []
    for node in graph.action_nodes():
        dependencies: list[str] = []
        for dep_node_id in graph.dep_list[node.id]:
            base_ref = graph.node_map[dep_node_id].ref
            for edge in graph.edges:
                if edge.source != dep_node_id or edge.target != node.id:
                    continue
                if edge.source_handle == EdgeType.ERROR:
                    ref = dep_from_edge_components(base_ref, edge.source_handle)
                else:
                    ref = base_ref
                dependencies.append(ref)
        dependencies = sorted(dependencies)

        action = ref2action[node.ref]
        control_flow = ActionControlFlow.model_validate(action.control_flow)
        action_stmt = ActionStatement(
            id=action.id,
            ref=node.ref,
            action=node.data.type,
            args=action.inputs,
            depends_on=dependencies,
            run_if=control_flow.run_if,
            for_each=control_flow.for_each,
            retry_policy=control_flow.retry_policy,
            start_delay=control_flow.start_delay,
            join_strategy=control_flow.join_strategy,
        )
        statements.append(action_stmt)
    return statements


def create_default_dsl_context(
    INPUTS: dict[str, Any] | None = None,
    ACTIONS: dict[str, Any] | None = None,
    TRIGGER: dict[str, Any] | None = None,
    ENV: DSLEnvironment | None = None,
) -> DSLContext:
    return DSLContext(
        INPUTS=INPUTS or {},
        ACTIONS=ACTIONS or {},
        TRIGGER=TRIGGER or {},
        ENV=cast(DSLEnvironment, ENV or {}),
    )


def dsl_execution_error_from_exception(e: BaseException) -> DSLExecutionError:
    return DSLExecutionError(
        is_error=True,
        type=e.__class__.__name__,
        message=str(e),
    )
