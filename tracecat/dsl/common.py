"""Tracecat DSL Common Module."""

from __future__ import annotations

from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from temporalio.client import Client, TLSConfig

from tracecat import config
from tracecat.dsl._converter import pydantic_data_converter
from tracecat.expressions import TemplateValidator

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


class DSLError(ValueError):
    pass


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


class DSLInput(BaseModel):
    """DSL definition for a workflow.

    The difference between this and a normal workflow engine is that here,
    our workflow execution order is defined by the DSL itself, independent
    of a workflow scheduler.

    With a traditional
    This allows the execution of the workflow to be fully deterministic.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)
    title: str
    description: str
    entrypoint: str
    actions: list[ActionStatement]
    config: DSLConfig = Field(default_factory=DSLConfig)
    triggers: list[Trigger] = Field(default_factory=list)
    inputs: dict[str, Any] = Field(
        default_factory=dict, description="Static input parameters"
    )
    trigger_inputs: dict[str, Any] = Field(
        default_factory=dict, description="Dynamic input parameters"
    )

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
            raise ValueError(f"Invalid file/path type {type(path)}")
        dsl_dict = yaml.safe_load(yaml_str)
        return DSLInput.model_validate(dsl_dict)

    def to_yaml(self, path: str | Path) -> None:
        with Path(path).expanduser().resolve().open("w") as f:
            yaml.dump(self.model_dump(), f)

    def dump_yaml(self) -> str:
        return yaml.dump(self.model_dump())

    @model_validator(mode="after")
    def validate_input(self) -> Self:
        if not self.actions:
            raise DSLError("At least one action must be defined")
        if len({action.ref for action in self.actions}) != len(self.actions):
            raise DSLError("All task.ref must be unique")
        valid_actions = tuple(action.ref for action in self.actions)
        if self.entrypoint not in valid_actions:
            raise DSLError(f"Entrypoint must be one of the actions {valid_actions!r}")
        n_entrypoints = sum(1 for action in self.actions if not action.depends_on)
        if n_entrypoints != 1:
            raise DSLError(f"Expected 1 entrypoint, got {n_entrypoints}")
        return self
