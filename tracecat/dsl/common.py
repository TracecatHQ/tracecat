"""Tracecat DSL Common Module."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import Annotated, Any, Literal, Self

import fsspec
import yaml
from pydantic import BaseModel, Field, model_validator
from temporalio.client import Client, TLSConfig

from tracecat import config
from tracecat.dsl._converter import pydantic_data_converter
from tracecat.expressions import TemplateValidator
from tracecat.logging import logger
from tracecat.types.exceptions import TracecatDSLError

SLUG_PATTERN = r"^[a-z0-9_]+$"
ACTION_TYPE_PATTERN = r"^[a-z0-9_.]+$"


async def get_temporal_client() -> Client:
    tls_config = False
    if config.TEMPORAL__TLS_ENABLED:
        tls_config = TLSConfig(
            client_cert=config.TEMPORAL__TLS_CLIENT_CERT,
            client_private_key=config.TEMPORAL__TLS_CLIENT_PRIVATE_KEY,
        )

    return await Client.connect(
        target_host=config.TEMPORAL__CLUSTER_URL,
        namespace=config.TEMPORAL__CLUSTER_NAMESPACE,
        tls=tls_config,
        data_converter=pydantic_data_converter,
    )


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
        pattern=ACTION_TYPE_PATTERN, description="Action type / UDF key"
    )

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


class Trigger(BaseModel):
    type: Literal["schedule", "webhook"]
    ref: str = Field(pattern=SLUG_PATTERN)
    args: dict[str, Any] = Field(default_factory=dict)


class ActionTest(BaseModel):
    ref: str = Field(..., pattern=SLUG_PATTERN, description="Action reference")
    enable: bool = Field(default=True, description="Enable or disable the test")
    validate_args: bool = Field(default=True, description="Validate action arguments")
    success: Any = Field(
        ...,
        description=(
            "Patched success output. This can be any data structure."
            "If it's a fsspec file, it will be read and the contents will be used."
        ),
    )
    failure: Any = Field(default=None, description="Patched failure output")

    async def resolve_success_output(self) -> Any:
        output = self.success
        if isinstance(output, str):
            output = await asyncio.to_thread(resolve_string_or_uri, output)
        return output


def resolve_string_or_uri(string_or_uri: str) -> Any:
    try:
        of = fsspec.open(string_or_uri, "rb")
        with of as f:
            data = f.read()

        return json.loads(data)

    except (FileNotFoundError, ValueError) as e:
        if "protocol not known" in str(e).lower():
            raise DSLError(
                f"Failed to read fsspec file, protocol not known: {string_or_uri}"
            ) from e
        logger.info(
            "String input did not match fsspec, handling as normal",
            string_or_uri=string_or_uri,
            error=e,
        )
        return string_or_uri


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
    entrypoint: str = Field(..., description="The entrypoint action ref")
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
        except Exception as e:
            raise TracecatDSLError(e) from e

    def to_yaml(self, path: str | Path) -> None:
        with Path(path).expanduser().resolve().open("w") as f:
            yaml.dump(self.model_dump(), f)

    def dump_yaml(self) -> str:
        return yaml.dump(self.model_dump())

    @model_validator(mode="after")
    def validate_input(self) -> Self:
        if not self.actions:
            raise TracecatDSLError("At least one action must be defined")
        if len({action.ref for action in self.actions}) != len(self.actions):
            raise TracecatDSLError("All task.ref must be unique")
        valid_actions = tuple(action.ref for action in self.actions)
        if self.entrypoint not in valid_actions:
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
