"""Tracecat DSL Common Module."""

from __future__ import annotations

from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import Any, Self

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from tracecat.contexts import RunContext
from tracecat.dsl.graph import RFEdge, RFGraph, TriggerNode, UDFNode, UDFNodeData
from tracecat.dsl.models import ActionStatement, ActionTest, DSLConfig, Trigger
from tracecat.dsl.validation import SchemaValidatorFactory
from tracecat.expressions import patterns
from tracecat.identifiers import WorkflowID
from tracecat.logging import logger
from tracecat.parse import traverse_leaves
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatDSLError, TracecatValidationError


class DSLEntrypoint(BaseModel):
    ref: str = Field(..., description="The entrypoint action ref")
    expects: Any | None = Field(None, description="Expected trigger input shape")
    """Trigger input schema."""

    @field_validator("expects")
    def validate_expects(cls, expects: Any) -> Any:
        if not expects:
            return expects
        logger.trace("Validating expects", expects=expects)
        try:
            factory = SchemaValidatorFactory(expects)
            _ = factory.create()
            return expects
        except* TracecatValidationError as eg:
            logger.error(
                "Failed to validate `entrypoint.expects`", errors=eg.exceptions
            )
            raise eg


class DSLInput(BaseModel):
    """DSL definition for a workflow.

    The difference between this and a normal workflow engine is that here,
    our workflow execution order is defined by the DSL itself, independent
    of a workflow scheduler.

    With a traditional
    This allows the execution of the workflow to be fully deterministic.
    """

    title: str
    description: str
    entrypoint: DSLEntrypoint
    actions: list[ActionStatement]
    config: DSLConfig = Field(default_factory=DSLConfig)
    triggers: list[Trigger] = Field(default_factory=list)
    inputs: dict[str, Any] = Field(
        default_factory=dict, description="Static input parameters"
    )
    tests: list[ActionTest] = Field(default_factory=list, description="Action tests")
    returns: Any | None = Field(None, description="The action ref or value to return.")

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
        valid_actions = tuple(action.ref for action in self.actions)
        if self.entrypoint.ref not in valid_actions:
            raise TracecatDSLError(
                f"Entrypoint reference must be one of the actions {valid_actions!r}"
            )
        n_entrypoints = sum(1 for action in self.actions if not action.depends_on)
        if n_entrypoints != 1:
            raise TracecatDSLError(f"Expected 1 entrypoint, got {n_entrypoints}")
        # Validate that all the refs in tests are valid actions
        valid_actions = {a.ref for a in self.actions}
        invalid_refs = {t.ref for t in self.tests} - valid_actions
        if invalid_refs:
            raise TracecatDSLError(f"Invalid action refs in tests: {invalid_refs}")

        # Validate that all the refs in depends_on are valid actions
        dependencies = {dep for a in self.actions for dep in a.depends_on}
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
        nodes: list[RFEdge] = [trigger_node]
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
    dsl: DSLInput
    wf_id: WorkflowID
    trigger_inputs: dict[str, Any] | None = None
    parent_run_context: RunContext | None = None
    run_config: dict[str, Any] = Field(default_factory=dict)
