from __future__ import annotations

import inspect
from collections.abc import Mapping
from pathlib import Path
from types import CoroutineType, FunctionType, MethodType
from typing import Annotated, Any, Generic, Literal, TypedDict, TypeVar, cast

import yaml
from pydantic import (
    UUID4,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    computed_field,
    model_validator,
)
from tracecat_registry import RegistrySecret, RegistryValidationError

from tracecat import config
from tracecat.db.schemas import RegistryAction
from tracecat.expressions.expectations import ExpectedField, create_expectation_model
from tracecat.logger import logger
from tracecat.types.exceptions import TracecatValidationError
from tracecat.types.validation import ValidationResult

ArgsT = TypeVar("ArgsT", bound=Mapping[str, Any])
ArgsClsT = TypeVar("ArgsClsT", bound=type[BaseModel])


"""Registry related"""


class BoundRegistryAction(BaseModel, Generic[ArgsClsT]):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    # Bound Implementation
    fn: FunctionType | MethodType | CoroutineType
    template_action: TemplateAction | None = None
    # Registry action details
    name: str
    description: str
    namespace: str
    type: Literal["udf", "template"]
    # Registry details
    version: str
    origin: str
    # Secrets
    secrets: list[RegistrySecret] | None = None
    # Bound Interface
    args_cls: ArgsClsT
    args_docs: dict[str, str] = Field(default_factory=dict)
    rtype_cls: Any | None = None
    rtype_adapter: TypeAdapter[Any] | None = None
    # Presentation
    default_title: str | None
    display_group: str | None
    # Options
    include_in_schema: bool = True

    @property
    def is_async(self) -> bool:
        return inspect.iscoroutinefunction(self.fn)

    @property
    def is_template(self) -> bool:
        return self.type == "template" and self.template_action is not None

    @property
    def action(self) -> str:
        return f"{self.namespace}.{self.name}"

    def to_database(self) -> RegistryAction:
        secrets = (
            None
            if not self.secrets
            else [secret.model_dump() for secret in self.secrets]
        )
        return RegistryAction(
            owner_id=config.TRACECAT__DEFAULT_ORG_ID,
            name=self.name,
            description=self.description,
            type=self.type,
            namespace=self.namespace,
            version=self.version,
            origin=self.origin,
            default_title=self.default_title,
            display_group=self.display_group,
            secrets=secrets,
            interface=self.get_interface(),
            implementation=self.get_implementation(),
        )

    def get_interface(self) -> RegistryActionInterface:
        if self.type == "template":
            if not self.template_action:
                raise ValueError("Template action is not set")
            expects = create_expectation_model(
                self.template_action.definition.expects,
                self.template_action.definition.action.replace(".", "__"),
            )
            return RegistryActionInterface(
                expects=expects.model_json_schema(),
                returns=self.template_action.definition.returns,
            )
        elif self.type == "udf":
            return RegistryActionInterface(
                expects=self.args_cls.model_json_schema(),
                returns=None
                if not self.rtype_adapter
                else self.rtype_adapter.json_schema(),
            )
        else:
            raise ValueError(f"Invalid registry action type: {self.type}")

    def get_implementation(self) -> AnnotatedRegistryActionImpl:
        if self.type == "template":
            if not self.template_action:
                raise ValueError("Template action is not set")
            return RegistryActionTemplateImpl(
                type="template",
                template_action=self.template_action,
            )
        elif self.type == "udf":
            module_path = inspect.getmodule(self.fn).__name__
            function_name = self.fn.__name__
            return RegistryActionUDFImpl(
                type="udf",
                url=self.origin,
                module=module_path,
                name=function_name,
            )
        else:
            raise ValueError(f"Invalid registry action type: {self.type}")

    def validate_args[T](self, *args, **kwargs) -> T:
        """Validate the input arguments for a Bound registry action.

        Checks:
        1. The Bound registry action must be called with keyword arguments only.
        2. The input arguments must be validated against the Bound registry action's model.
        """
        if len(args) > 0:
            raise RegistryValidationError(
                "Bound registry action must be called with keyword arguments.",
                key=self.action,
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
            logger.error(
                f"Validation error for bound registry action {self.action!r}. {e.errors()!r}"
            )
            raise RegistryValidationError(
                f"Validation error for bound registry action {self.action!r}. {e.errors()!r}",
                key=self.action,
                err=e,
            ) from e
        except Exception as e:
            raise RegistryValidationError(
                f"Unexpected error when validating input arguments for bound registry action {self.action!r}. {e}",
                key=self.action,
            ) from e


# Templates


class ActionStep(BaseModel):
    ref: str = Field(..., description="The reference of the step")
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
    steps: list[ActionStep] = Field(
        ..., description="The sequence of steps for the action"
    )
    returns: str | list[str] | dict[str, Any] = Field(
        ..., description="The result of the action"
    )

    # Validate steps
    @model_validator(mode="after")
    def validate_steps(self) -> TemplateActionDefinition:
        step_refs = [step.ref for step in self.steps]
        unique_step_refs = set(step_refs)

        if len(step_refs) != len(unique_step_refs):
            duplicate_step_refs = [ref for ref in step_refs if step_refs.count(ref) > 1]
            raise ValueError(f"Duplicate step references found: {duplicate_step_refs}")

        return self

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

    def to_yaml(self) -> str:
        return yaml.dump(self.model_dump(mode="json"))

    @staticmethod
    def from_db(template_action: RegistryAction) -> TemplateAction:
        intf = template_action.interface
        impl = template_action.implementation
        return TemplateAction(
            type="action",
            origin=template_action.origin,
            definition=TemplateActionDefinition(
                name=template_action.name,
                namespace=template_action.namespace,
                title=template_action.default_title,
                description=template_action.description,
                display_group=template_action.display_group,
                secrets=template_action.secrets,
                expects=intf.get("expects", {}),
                returns=intf.get("returns", {}),
                steps=impl.get("steps", []),
            ),
        )


# API models


class RegistryActionBase(BaseModel):
    """API read model for a registered action."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    name: str = Field(..., description="The name of the action")
    description: str = Field(..., description="The description of the action")
    namespace: str = Field(..., description="The namespace of the action")
    type: Literal["udf", "template"] = Field(..., description="The type of the action")
    version: str = Field(..., description="Registry version")
    origin: str = Field(..., description="The origin of the action as a url")
    secrets: list[RegistrySecret] | None = Field(
        None, description="The secrets required by the action"
    )
    interface: RegistryActionInterface
    implementation: AnnotatedRegistryActionImpl
    default_title: str | None = Field(
        None, description="The default title of the action"
    )
    display_group: str | None = Field(
        None, description="The presentation group of the action"
    )
    options: RegistryActionOptions = Field(
        default_factory=lambda: RegistryActionOptions(),
        description="The options for the action",
    )
    repository_id: UUID4 = Field(..., description="The repository id")


class RegistryActionRead(RegistryActionBase):
    """API read model for a registered action."""

    @computed_field(return_type=str)
    @property
    def action(self) -> str:
        """The full action identifier."""
        return f"{self.namespace}.{self.name}"

    @computed_field(return_type=bool)
    @property
    def is_template(self) -> bool:
        """Whether the action is a template."""
        return self.implementation.type == "template"

    @staticmethod
    def from_bound(
        action: BoundRegistryAction, repository_id: UUID4
    ) -> RegistryActionRead:
        return RegistryActionRead(
            repository_id=repository_id,
            name=action.name,
            description=action.description,
            namespace=action.namespace,
            type=action.type,
            secrets=action.secrets,
            interface=action.get_interface(),
            implementation=action.get_implementation(),
            default_title=action.default_title,
            display_group=action.display_group,
            version=action.version,
            origin=action.origin,
            options=RegistryActionOptions(include_in_schema=action.include_in_schema),
        )

    @staticmethod
    def from_database(action: RegistryAction) -> RegistryActionRead:
        return RegistryActionRead(
            repository_id=action.repository_id,
            name=action.name,
            description=action.description,
            namespace=action.namespace,
            type=action.type,
            interface=model_converters.db_to_interface(action),
            implementation=action.implementation,
            default_title=action.default_title,
            display_group=action.display_group,
            version=action.version,
            origin=action.origin,
            options=action.options,
            secrets=action.secrets,
        )


class RegistryActionCreate(RegistryActionBase):
    """API create model for a registered action."""

    @staticmethod
    def from_bound(
        action: BoundRegistryAction, repository_id: UUID4
    ) -> RegistryActionCreate:
        return RegistryActionCreate(
            repository_id=repository_id,
            name=action.name,
            description=action.description,
            namespace=action.namespace,
            type=action.type,
            interface=action.get_interface(),
            implementation=action.get_implementation(),
            default_title=action.default_title,
            display_group=action.display_group,
            version=action.version,
            origin=action.origin,
            secrets=action.secrets,
            options=RegistryActionOptions(include_in_schema=action.include_in_schema),
        )


class RegistryActionUpdate(BaseModel):
    """API update model for a registered action."""

    name: str | None = Field(None, description="Update the name of the action")
    description: str | None = Field(
        None, description="Update the description of the action"
    )
    secrets: list[RegistrySecret] | None = Field(
        None, description="Update the secrets of the action"
    )
    interface: RegistryActionInterface | None = Field(
        None, description="Update the interface of the action"
    )
    implementation: AnnotatedRegistryActionImpl | None = Field(
        None, description="Update the implementation of the action"
    )
    default_title: str | None = Field(
        None, description="Update the default title of the action"
    )
    display_group: str | None = Field(
        None, description="Update the display group of the action"
    )
    options: RegistryActionOptions | None = Field(
        None, description="Update the options of the action"
    )


class RegistryActionValidate(BaseModel):
    registry_version: str
    args: dict[str, Any]


class RegistryActionValidateResponse(BaseModel):
    ok: bool
    message: str
    detail: Any | None = None

    @staticmethod
    def from_validation_result(
        result: ValidationResult,
    ) -> RegistryActionValidateResponse:
        return RegistryActionValidateResponse(
            ok=result.status == "success",
            message=result.msg,
            # Dump this to get subclass-specific fields
            detail=result.model_dump(exclude={"status", "msg"}, exclude_none=True),
        )

    @staticmethod
    def from_dsl_validation_error(exc: TracecatValidationError):
        return RegistryActionValidateResponse(
            ok=False, message=str(exc), detail=exc.detail
        )

    @staticmethod
    def from_pydantic_validation_error(exc: ValidationError):
        return RegistryActionValidateResponse(
            ok=False,
            message=f"Schema validation error: {exc.title}",
            detail=exc.errors(),
        )


"""DB Schema related """
# These classes will be used for the db
# UDFs can only store reference to a function in a package


class RegistryActionOptions(BaseModel):
    include_in_schema: bool = True


class RegistryActionInterface(TypedDict):
    expects: dict[str, Any]
    returns: Any


class RegistryActionUDFImpl(BaseModel):
    type: Literal["udf"] = Field("udf", frozen=True)
    url: str = Field(..., description="The package url")
    module: str = Field(..., description="The module name")
    name: str = Field(..., description="The name of the UDF function name")


class RegistryActionTemplateImpl(BaseModel):
    type: Literal["template"] = Field("template", frozen=True)
    template_action: TemplateAction = Field(..., description="The template action")


RegistryActionImpl = RegistryActionTemplateImpl | RegistryActionUDFImpl
AnnotatedRegistryActionImpl = Annotated[
    RegistryActionImpl, Field(..., discriminator="type")
]
RegistryActionImplValidator: TypeAdapter[RegistryActionImpl] = TypeAdapter(
    AnnotatedRegistryActionImpl
)


class model_converters:
    """Converters for the registry action models."""

    def __new__(cls):
        raise NotImplementedError("This class is not instantiable")

    @staticmethod
    def implementation_to_interface(
        impl: AnnotatedRegistryActionImpl,
    ) -> RegistryActionInterface:
        if impl.type == "template":
            expects = create_expectation_model(
                schema=impl.template_action.definition.expects,
                model_name=impl.template_action.definition.action.replace(".", "__"),
            )
            return RegistryActionInterface(
                expects=expects.model_json_schema(),
                returns=impl.template_action.definition.returns,
            )
        else:
            return RegistryActionInterface(
                expects=impl.interface.get("expects", {}),
                returns=impl.interface.get("returns", {}),
            )

    @staticmethod
    def db_to_interface(action: RegistryAction) -> RegistryActionInterface:
        match action.implementation:
            case {"type": "template", "template_action": template_action}:
                template = TemplateAction.model_validate(template_action)
                expects = create_expectation_model(
                    template.definition.expects,
                    template.definition.action.replace(".", "__"),
                )
                intf = RegistryActionInterface(
                    expects=expects.model_json_schema(),
                    returns=template.definition.returns,
                )
            case {"type": "udf", **_kwargs}:
                intf = RegistryActionInterface(
                    expects=action.interface.get("expects", {}),
                    returns=action.interface.get("returns", {}),
                )
            case _:
                raise ValueError(
                    f"Unknown implementation type: {action.implementation}"
                )
        return intf
