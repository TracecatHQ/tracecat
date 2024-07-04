"""Tracecat DSL Common Module."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine
from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import Annotated, Any, ClassVar, Literal, Self, TypedDict

import fsspec
import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from tracecat.expressions import patterns
from tracecat.expressions.validators import TemplateValidator
from tracecat.logging import logger
from tracecat.parse import traverse_leaves
from tracecat.types.exceptions import TracecatDSLError
from tracecat.types.validation import VALIDATION_TYPES

SLUG_PATTERN = r"^[a-z0-9_]+$"
ACTION_TYPE_PATTERN = r"^[a-z0-9_.]+$"


class ActionStatement(BaseModel):
    id: str | None = Field(
        default=None,
        exclude=True,
        description=(
            "The action ID. If this is populated means there is a corresponding action"
            "in the database `Action` table."
        ),
    )

    ref: str = Field(pattern=SLUG_PATTERN, description="Unique reference for the task")

    description: str = ""

    action: str = Field(
        pattern=ACTION_TYPE_PATTERN,
        description="Action type. Equivalent to the UDF key.",
    )
    """Action type. Equivalent to the UDF key."""

    args: dict[str, Any] = Field(
        default_factory=dict, description="Arguments for the action"
    )

    depends_on: list[str] = Field(default_factory=list, description="Task dependencies")

    run_if: Annotated[
        str | None,
        Field(default=None, description="Condition to run the task"),
        TemplateValidator(),
    ]

    for_each: Annotated[
        str | list[str] | None,
        Field(
            default=None,
            description="Iterate over a list of items and run the task for each item.",
        ),
        TemplateValidator(),
    ]

    @property
    def title(self) -> str:
        return self.ref.capitalize().replace("_", " ")


class DSLConfig(BaseModel):
    scheduler: Literal["static", "dynamic"] = "dynamic"
    enable_runtime_tests: bool = Field(
        default=False,
        description="Enable runtime action tests. This is dynamically set on workflow entry.",
    )


class Trigger(BaseModel):
    type: Literal["schedule", "webhook"]
    ref: str = Field(pattern=SLUG_PATTERN)
    args: dict[str, Any] = Field(default_factory=dict)


class ActionTest(BaseModel):
    ref: str = Field(..., pattern=SLUG_PATTERN, description="Action reference")
    enable: bool = True
    validate_args: bool = True
    success: Any = Field(
        ...,
        description=(
            "Patched success output. This can be any data structure."
            "If it's a fsspec file, it will be read and the contents will be used."
        ),
    )
    failure: Any = Field(default=None, description="Patched failure output")

    async def resolve_success_output(self) -> Any:
        def resolver_coro(_obj: Any) -> Coroutine:
            return asyncio.to_thread(resolve_string_or_uri, _obj)

        obj = self.success
        match obj:
            case str():
                return await resolver_coro(obj)
            case list():
                tasks = []
                async with asyncio.TaskGroup() as tg:
                    for item in obj:
                        task = tg.create_task(resolver_coro(item))
                        tasks.append(task)
                return [task.result() for task in tasks]
            case _:
                return obj


def resolve_string_or_uri(string_or_uri: str) -> Any:
    try:
        of = fsspec.open(string_or_uri, "rb")
        with of as f:
            data = f.read()

        return json.loads(data)

    except (FileNotFoundError, ValueError) as e:
        if "protocol not known" in str(e).lower():
            raise TracecatDSLError(
                f"Failed to read fsspec file, protocol not known: {string_or_uri}"
            ) from e
        logger.info(
            "String input did not match fsspec, handling as normal",
            string_or_uri=string_or_uri,
            error=e,
        )
        return string_or_uri


class DSLEntrypoint(BaseModel):
    SUPPORTED_VALIDATION_TYPES: ClassVar[dict[str, type]] = VALIDATION_TYPES
    ref: str = Field(..., description="The entrypoint action ref")
    expects: Any | None = Field(None, description="Expected trigger input shape")
    """Trigger input schema."""

    @field_validator("expects")
    def validate_expects(cls, expects: Any) -> dict[str, Any]:
        logger.info("Validating expects", expects=expects)
        try:
            exceptions = []
            for loc, value in traverse_leaves(expects):
                if not isinstance(value, str):
                    # Check that the leaf values are strings
                    exceptions.append(
                        TracecatDSLError(
                            f"`entrypoint.expects` values must be strings, but found {value!r} of type {type(value)}",
                            detail=loc,
                        )
                    )
                if value not in DSLEntrypoint.SUPPORTED_VALIDATION_TYPES:
                    # Check that the leaf values are valid string values like "str"
                    exceptions.append(
                        TracecatDSLError(
                            f"Invalid type for `entrypoint.expects` value {value!r}",
                            detail=loc,
                        )
                    )
            if exceptions:
                raise ExceptionGroup(
                    "Entrypoint expects context validation failed", exceptions
                )
            return expects
        except* TracecatDSLError as eg:
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
    trigger_inputs: dict[str, Any] = Field(
        default_factory=dict, description="Dynamic input parameters"
    )
    tests: list[ActionTest] = Field(default_factory=list, description="Action tests")

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

    @model_validator(mode="after")
    def validate_structure(self) -> Self:
        if not self.actions:
            raise TracecatDSLError("At least one action must be defined")
        if len({action.ref for action in self.actions}) != len(self.actions):
            raise TracecatDSLError("All task.ref must be unique")
        valid_actions = tuple(action.ref for action in self.actions)
        if self.entrypoint.ref not in valid_actions:
            raise TracecatDSLError(
                f"Entrypoint must be one of the actions {valid_actions!r}"
            )
        n_entrypoints = sum(1 for action in self.actions if not action.depends_on)
        if n_entrypoints != 1:
            raise TracecatDSLError(f"Expected 1 entrypoint, got {n_entrypoints}")
        # Validate that all the refs in tests are valid actions
        valid_actions = {a.ref for a in self.actions}
        invalid_refs = {t.ref for t in self.tests} - valid_actions
        if invalid_refs:
            raise TracecatDSLError(f"Invalid action refs in tests: {invalid_refs}")
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


class DSLNodeResult(TypedDict):
    result: Any
    result_typename: str
