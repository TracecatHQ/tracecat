from __future__ import annotations

import inspect
from collections.abc import Mapping
from pathlib import Path
from types import CoroutineType, FunctionType, MethodType
from typing import Any, Generic, Literal, TypedDict, TypeVar, cast

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    computed_field,
    model_validator,
)
from pydantic_core import ValidationError
from tracecat_registry import RegistrySecret, RegistryValidationError

from tracecat import config
from tracecat.db.schemas import UDFSpec
from tracecat.expressions.expectations import ExpectedField
from tracecat.identifiers import OwnerID
from tracecat.logger import logger

ArgsT = TypeVar("ArgsT", bound=Mapping[str, Any])
ArgsClsT = TypeVar("ArgsClsT", bound=type[BaseModel])


class RegisteredUDF(BaseModel, Generic[ArgsClsT]):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    fn: FunctionType | MethodType | CoroutineType
    key: str
    description: str
    namespace: str
    version: str
    secrets: list[RegistrySecret] | None = None
    args_cls: ArgsClsT
    args_docs: dict[str, str] = Field(default_factory=dict)
    rtype_cls: Any | None = None
    rtype_adapter: TypeAdapter[Any] | None = None
    metadata: RegisteredUDFMetadata = Field(default_factory=dict)
    template_action: TemplateAction | None = None

    @property
    def is_async(self) -> bool:
        return inspect.iscoroutinefunction(self.fn)

    def construct_schema(self) -> dict[str, Any]:
        return UDFSchema(
            args=self.args_cls.model_json_schema(),
            rtype=None if not self.rtype_adapter else self.rtype_adapter.json_schema(),
            secrets=self.secrets,
            version=self.version,
            description=self.description,
            namespace=self.namespace,
            key=self.key,
            metadata=self.metadata,
        ).model_dump(mode="json")

    def to_udf_spec(
        self, owner_id: OwnerID = config.TRACECAT__DEFAULT_USER_ID
    ) -> UDFSpec:
        return UDFSpec(
            owner_id=owner_id,
            key=self.key,
            description=self.description,
            namespace=self.namespace,
            version=self.version,
            json_schema=self.construct_schema(),
            meta=self.metadata,
        )

    def validate_args[T](self, *args, **kwargs) -> T:
        """Validate the input arguments for a UDF.

        Checks:
        1. The UDF must be called with keyword arguments only.
        2. The input arguments must be validated against the UDF's model.
        """
        if len(args) > 0:
            raise RegistryValidationError(
                "UDF must be called with keyword arguments.", key=self.key
            )

        # Validate the input arguments, fail early if the input is invalid
        # Note that we've added TemplateValidator to the list of validators
        # so template expressions will pass args model validation
        try:
            # Note that we're allowing type coercion for the input arguments
            # Use cases would be transforming a UTC string to a datetime object
            # We return the validated input arguments as a dictionary
            validated: BaseModel = self.args_cls.model_validate(kwargs)
            return cast(T, validated.model_dump())
        except ValidationError as e:
            logger.error(f"Validation error for UDF {self.key!r}. {e.errors()!r}")
            raise RegistryValidationError(
                f"Validation error for UDF {self.key!r}. {e.errors()!r}",
                key=self.key,
                err=e,
            ) from e
        except Exception as e:
            raise RegistryValidationError(
                f"Unexpected error when validating input arguments for UDF {self.key!r}. {e}",
                key=self.key,
            ) from e


class RegisteredUDFMetadata(TypedDict, total=False):
    """Metadata for a registered UDF."""

    is_template: bool
    default_title: str | None
    display_group: str | None
    include_in_schema: bool
    origin: str


class RegisteredUDFRead(BaseModel):
    """API read model for a registered UDF."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    key: str
    description: str
    namespace: str
    version: str | None = None
    secrets: list[RegistrySecret] | None = None
    args_docs: dict[str, str] = Field(default_factory=dict)
    metadata: RegisteredUDFMetadata = Field(default_factory=dict)


class RunActionParams(BaseModel):
    """Arguments for a UDF."""

    args: dict[str, Any] | None = None
    context: dict[str, Any] | None = None


class UDFSchema(BaseModel):
    args: dict[str, Any]
    rtype: dict[str, Any] | None
    secrets: list[RegistrySecret] | None
    version: str | None
    description: str
    namespace: str
    key: str
    metadata: RegisteredUDFMetadata


class ActionLayer(BaseModel):
    ref: str = Field(..., description="The reference of the layer")
    action: str
    args: ArgsT


class TemplateActionDefinition(BaseModel):
    name: str = Field(..., description="The action name")
    namespace: str = Field(..., description="The namespace of the action")
    title: str = Field(..., description="The title of the action")
    description: str = Field("", description="The description of the action")
    display_group: str = Field(..., description="The display group of the action")
    secrets: list[RegistrySecret] | None = Field(
        None, description="The secrets to pass to the action"
    )
    expects: dict[str, ExpectedField] = Field(
        ..., description="The arguments to pass to the action"
    )
    layers: list[ActionLayer] = Field(
        ..., description="The internal layers of the action"
    )
    returns: str | list[str] | dict[str, Any] = Field(
        ..., description="The result of the action"
    )

    # Validate layers
    @model_validator(mode="after")
    def validate_layers(self) -> TemplateActionDefinition:
        layer_refs = [layer.ref for layer in self.layers]
        unique_layer_refs = set(layer_refs)

        if len(layer_refs) != len(unique_layer_refs):
            duplicate_layer_refs = [
                ref for ref in layer_refs if layer_refs.count(ref) > 1
            ]
            raise ValueError(
                f"Duplicate layer references found: {duplicate_layer_refs}"
            )

        return self

    @computed_field
    @property
    def action(self) -> str:
        return f"{self.namespace}.{self.name}"


class TemplateAction(BaseModel):
    type: Literal["action"] = Field("action", frozen=True)
    definition: TemplateActionDefinition

    @staticmethod
    def from_yaml(path: Path) -> TemplateAction:
        with path.open("r") as f:
            template = yaml.safe_load(f)

        return TemplateAction(**template)
