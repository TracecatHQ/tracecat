from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, TypedDict

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
from tracecat_registry import RegistrySecret, RegistrySecretType

from tracecat.exceptions import TracecatValidationError
from tracecat.expressions.schemas import ExpectedField
from tracecat.registry.actions.enums import TemplateActionValidationErrorType
from tracecat.validation.schemas import (
    ActionValidationResult,
    TemplateActionExprValidationResult,
    ValidationDetail,
)

if TYPE_CHECKING:
    from uuid import UUID

    from tracecat.db.models import BaseRegistryIndex
    from tracecat.registry.actions.bound import BoundRegistryAction
    from tracecat.registry.actions.types import IndexEntry
    from tracecat.registry.versions.schemas import RegistryVersionManifestAction


RegistryActionType = Literal["udf", "template"]


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
    secrets: list[RegistrySecretType] | None = Field(
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
    secrets: list[RegistrySecretType] | None = Field(
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


class RegistryActionReadMinimal(BaseModel):
    """API minimal read model for a registered action."""

    id: UUID4 = Field(..., description="The registry action id")
    name: str = Field(..., description="The name of the action")
    description: str = Field(..., description="The description of the action")
    namespace: str = Field(..., description="The namespace of the action")
    type: RegistryActionType = Field(..., description="The type of the action")
    origin: str = Field(..., description="The origin of the action as a url")
    default_title: str | None = Field(
        None, description="The default title of the action"
    )
    display_group: str | None = Field(
        None, description="The presentation group of the action"
    )

    @computed_field(return_type=str)
    @property
    def action(self) -> str:
        """The full action identifier."""
        return f"{self.namespace}.{self.name}"

    @classmethod
    def from_index(cls, index: IndexEntry, origin: str) -> RegistryActionReadMinimal:
        """Create from an IndexEntry object."""
        from typing import cast

        return cls(
            id=index.id,
            name=index.name,
            description=index.description,
            namespace=index.namespace,
            type=cast(RegistryActionType, index.action_type),
            origin=origin,
            default_title=index.default_title,
            display_group=index.display_group,
        )


class RegistryActionRead(RegistryActionBase):
    """API read model for a registered action."""

    id: UUID4 = Field(..., description="The registry action id")

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

    @classmethod
    def from_index_and_manifest(
        cls,
        index: BaseRegistryIndex | IndexEntry,
        manifest_action: RegistryVersionManifestAction,
        origin: str,
        repository_id: UUID,
        extra_secrets: list[RegistrySecretType] | None = None,
    ) -> RegistryActionRead:
        """Create from BaseRegistryIndex or IndexEntry + manifest action.

        Args:
            index: The registry index entry
            manifest_action: The manifest action data
            origin: The origin URL
            repository_id: The repository ID
            extra_secrets: Additional secrets to merge (e.g., from template steps)
        """

        # Merge direct secrets with extra secrets (e.g., from template steps)
        all_secrets = list(manifest_action.secrets) if manifest_action.secrets else []
        if extra_secrets:
            # Use a set to dedupe by secret identity
            seen = {
                (s.name if isinstance(s, RegistrySecret) else s.provider_id)
                for s in all_secrets
            }
            for secret in extra_secrets:
                key = (
                    secret.name
                    if isinstance(secret, RegistrySecret)
                    else secret.provider_id
                )
                if key not in seen:
                    all_secrets.append(secret)
                    seen.add(key)

        return cls(
            id=index.id,
            name=index.name,
            description=index.description,
            namespace=index.namespace,
            type=manifest_action.action_type,
            origin=origin,
            secrets=all_secrets if all_secrets else None,
            interface=manifest_action.interface,
            implementation=RegistryActionImplValidator.validate_python(
                manifest_action.implementation
            ),
            default_title=index.default_title,
            display_group=index.display_group,
            doc_url=index.doc_url,
            author=index.author,
            deprecated=index.deprecated,
            options=RegistryActionOptions(**index.options)
            if index.options
            else RegistryActionOptions(),
            repository_id=repository_id,
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
            options=RegistryActionOptions(
                include_in_schema=action.include_in_schema,
                requires_approval=action.requires_approval,
                required_entitlements=action.required_entitlements,
            ),
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
    secrets: list[RegistrySecretType] | None = Field(
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
            options=RegistryActionOptions(
                include_in_schema=action.include_in_schema,
                requires_approval=action.requires_approval,
            ),
            secrets=action.secrets,
        )


class RegistryActionValidateResponse(BaseModel):
    ok: bool
    message: str
    detail: list[ValidationDetail] | None = None
    action_ref: str | None = None

    @staticmethod
    def from_validation_result(
        result: ActionValidationResult,
    ) -> RegistryActionValidateResponse:
        return RegistryActionValidateResponse(
            ok=result.status == "success",
            message=result.msg,
            # Dump this to get subclass-specific fields
            detail=result.detail,
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
            detail=ValidationDetail.list_from_pydantic(exc),
        )


"""DB Schema related """
# These classes will be used for the db
# UDFs can only store reference to a function in a package


class RegistryActionOptions(BaseModel):
    include_in_schema: bool = True
    requires_approval: bool = False
    required_entitlements: list[str] | None = None


class RegistryActionInterface(TypedDict):
    expects: dict[str, Any]
    returns: Any


RegistryActionInterfaceValidator: TypeAdapter[RegistryActionInterface] = TypeAdapter(
    RegistryActionInterface
)


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
            loc_primary=".".join(map(str, v.loc)),
            loc_secondary=v.ref,
        )
