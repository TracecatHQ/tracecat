from __future__ import annotations

import inspect
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict, TypeVar, cast

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
from tracecat_registry import RegistrySecret

from tracecat.db.schemas import RegistryAction
from tracecat.expressions.expectations import ExpectedField, create_expectation_model
from tracecat.logger import logger
from tracecat.registry.actions.enums import TemplateActionValidationErrorType
from tracecat.types.exceptions import (
    RegistryActionError,
    RegistryValidationError,
    TracecatValidationError,
)
from tracecat.validation.models import (
    TemplateActionExprValidationResult,
    ValidationResult,
)

ArgsClsT = TypeVar("ArgsClsT", bound=type[BaseModel])
RegistryActionType = Literal["udf", "template"]

"""Registry related"""


class BoundRegistryAction(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    # Bound Implementation
    fn: Callable[..., Any]
    template_action: TemplateAction | None = None
    # Registry action details
    name: str
    description: str
    namespace: str
    type: RegistryActionType
    # Registry details
    origin: str
    # Secrets
    secrets: list[RegistrySecret] | None = None
    # Bound Interface
    args_cls: type[BaseModel]
    args_docs: dict[str, str] = Field(default_factory=dict)
    rtype_cls: Any | None = None
    rtype_adapter: TypeAdapter[Any] | None = None
    # Presentation
    default_title: str | None
    display_group: str | None
    doc_url: str | None
    author: str | None
    deprecated: str | None
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
            module = inspect.getmodule(self.fn)
            if not module:
                raise RegistryActionError("UDF module not found")
            module_path = module.__name__
            function_name = self.fn.__name__
            return RegistryActionUDFImpl(
                type="udf",
                url=self.origin,
                module=module_path,
                name=function_name,
            )
        else:
            raise ValueError(f"Invalid registry action type: {self.type}")

    def validate_args(self, *args, **kwargs) -> dict[str, Any]:
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
            validated = self.args_cls.model_validate(kwargs)
            validated_args = validated.model_dump(mode="json")
            return validated_args
        except ValidationError as e:
            msg = (
                f"Validation error for bound registry action {self.action!r}."
                f"\n{json.dumps(e.errors(include_url=False), indent=2)}"
            )
            logger.error(msg)
            raise RegistryValidationError(msg, key=self.action, err=e) from e
        except Exception as e:
            raise RegistryValidationError(
                f"Unexpected error when validating input arguments for bound registry action {self.action!r}. {e!r}",
                key=self.action,
            ) from e


# Templates


class ActionStep(BaseModel):
    ref: str = Field(..., description="The reference of the step")
    action: str
    args: Mapping[str, Any]


class TemplateActionDefinition(BaseModel):
    name: str = Field(..., description="The action name")
    namespace: str = Field(..., description="The namespace of the action")
    title: str = Field(..., description="The title of the action")
    description: str = Field(default="", description="The description of the action")
    display_group: str = Field(..., description="The display group of the action")
    doc_url: str | None = Field(default=None, description="Link to documentation")
    author: str | None = Field(default=None, description="Author of the action")
    deprecated: str | None = Field(
        default=None,
        description="Marks action as deprecated along with message",
    )
    secrets: list[RegistrySecret] | None = Field(
        default=None, description="The secrets to pass to the action"
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
    def validate_steps(self):
        # Check for at least 1 step
        if not self.steps:
            raise TracecatValidationError("Template action must have at least 1 step")

        step_refs = [step.ref for step in self.steps]
        unique_step_refs = set(step_refs)

        # Check for duplicate step refs
        if len(step_refs) != len(unique_step_refs):
            duplicate_step_refs = [ref for ref in step_refs if step_refs.count(ref) > 1]
            raise TracecatValidationError(
                f"Duplicate step references found: {duplicate_step_refs}"
            )

        # Check if any step action references the template action
        template_action = f"{self.namespace}.{self.name}"
        if violating_steps := [s for s in self.steps if s.action == template_action]:
            raise TracecatValidationError(
                f"Steps cannot reference the template action itself: {template_action}."
                f"{len(violating_steps)} steps reference the template action: {violating_steps}"
            )

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


# API models


class RegistryActionBase(BaseModel):
    """API read model for a registered action."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    name: str = Field(
        ...,
        description="The name of the action",
        min_length=1,
        max_length=100,
    )
    description: str = Field(
        ...,
        description="The description of the action",
        max_length=1000,
    )
    namespace: str = Field(..., description="The namespace of the action")
    type: RegistryActionType = Field(..., description="The type of the action")
    origin: str = Field(
        ...,
        description="The origin of the action as a url",
        min_length=1,
        max_length=1000,
    )
    secrets: list[RegistrySecret] | None = Field(
        None, description="The secrets required by the action"
    )
    interface: RegistryActionInterface
    implementation: AnnotatedRegistryActionImpl
    default_title: str | None = Field(
        None,
        description="The default title of the action",
        min_length=1,
        max_length=100,
    )
    display_group: str | None = Field(
        None,
        description="The presentation group of the action",
        min_length=1,
        max_length=100,
    )
    doc_url: str | None = Field(
        None,
        description="Link to documentation",
        min_length=1,
        max_length=1000,
    )
    author: str | None = Field(
        None,
        description="Author of the action",
        min_length=1,
        max_length=100,
    )
    deprecated: str | None = Field(
        None,
        description="Marks action as deprecated along with message",
        min_length=1,
        max_length=1000,
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
    def from_database(
        action: RegistryAction, extra_secrets: list[RegistrySecret] | None = None
    ) -> RegistryActionRead:
        impl = RegistryActionImplValidator.validate_python(action.implementation)
        secrets = [RegistrySecret(**secret) for secret in action.secrets or []]
        if extra_secrets:
            secrets.extend(extra_secrets)
        return RegistryActionRead(
            repository_id=action.repository_id,
            name=action.name,
            description=action.description,
            namespace=action.namespace,
            type=cast(RegistryActionType, action.type),
            doc_url=action.doc_url,
            author=action.author,
            deprecated=action.deprecated,
            interface=model_converters.db_to_interface(action),
            implementation=impl,
            default_title=action.default_title,
            display_group=action.display_group,
            origin=action.origin,
            options=RegistryActionOptions(**action.options),
            secrets=secrets,
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
            doc_url=action.doc_url,
            author=action.author,
            deprecated=action.deprecated,
            origin=action.origin,
            secrets=action.secrets,
            options=RegistryActionOptions(include_in_schema=action.include_in_schema),
        )


class RegistryActionUpdate(BaseModel):
    """API update model for a registered action."""

    name: str | None = Field(
        default=None,
        description="Update the name of the action",
        min_length=1,
        max_length=100,
    )
    description: str | None = Field(
        default=None,
        description="Update the description of the action",
        max_length=1000,
    )
    secrets: list[RegistrySecret] | None = Field(
        default=None,
        description="Update the secrets of the action",
    )
    interface: RegistryActionInterface | None = Field(
        default=None,
        description="Update the interface of the action",
    )
    implementation: AnnotatedRegistryActionImpl | None = Field(
        default=None,
        description="Update the implementation of the action",
    )
    default_title: str | None = Field(
        default=None,
        description="Update the default title of the action",
        min_length=1,
        max_length=100,
    )
    display_group: str | None = Field(
        default=None,
        description="Update the display group of the action",
        min_length=1,
        max_length=100,
    )
    doc_url: str | None = Field(
        default=None,
        description="Update the doc url of the action",
        min_length=1,
        max_length=1000,
    )
    author: str | None = Field(
        default=None,
        description="Update the author of the action",
        min_length=1,
        max_length=100,
    )
    deprecated: str | None = Field(
        default=None,
        description="Update the deprecation message of the action",
        min_length=1,
        max_length=1000,
    )
    options: RegistryActionOptions | None = Field(
        default=None,
        description="Update the options of the action",
    )

    @staticmethod
    def from_bound(action: BoundRegistryAction) -> RegistryActionUpdate:
        return RegistryActionUpdate(
            name=action.name,
            description=action.description,
            interface=action.get_interface(),
            implementation=action.get_implementation(),
            default_title=action.default_title,
            display_group=action.display_group,
            doc_url=action.doc_url,
            author=action.author,
            deprecated=action.deprecated,
            options=RegistryActionOptions(include_in_schema=action.include_in_schema),
        )


class RegistryActionValidate(BaseModel):
    args: dict[str, Any]


class RegistryActionValidateResponse(BaseModel):
    ok: bool
    message: str
    detail: Any | None = None
    action_ref: str | None = None

    @staticmethod
    def from_validation_result(
        result: ValidationResult,
    ) -> RegistryActionValidateResponse:
        return RegistryActionValidateResponse(
            ok=result.status == "success",
            message=result.msg,
            # Dump this to get subclass-specific fields
            detail=result.model_dump(include={"detail"}, exclude_none=True),
            action_ref=result.ref,
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
)  # type: ignore


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
            return RegistryActionInterface(expects={}, returns={})

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


class RegistryActionValidationErrorInfo(BaseModel):
    type: TemplateActionValidationErrorType
    details: list[str]
    is_template: bool
    """Some context about where the error occurred"""
    loc_primary: str
    """Primary location of the error"""
    loc_secondary: str | None = None
    """Secondary location of the error. Displayed in parentheses next to the primary location."""

    @staticmethod
    def from_validation_result(
        v: TemplateActionExprValidationResult, is_template: bool = True
    ) -> RegistryActionValidationErrorInfo:
        return RegistryActionValidationErrorInfo(
            type=TemplateActionValidationErrorType.EXPRESSION_VALIDATION_ERROR,
            details=[v.msg],
            is_template=is_template,
            loc_primary=v.loc,
        )
