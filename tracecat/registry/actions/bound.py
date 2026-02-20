"""BoundRegistryAction - Runtime representation of a registry action.

This module is separate from schemas.py to avoid pulling in heavy dependencies
(lark, expressions) when only the API schemas are needed.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from tracecat_registry import RegistrySecretType

from tracecat.exceptions import RegistryActionError, RegistryValidationError
from tracecat.expressions.expectations import create_expectation_model
from tracecat.logger import logger
from tracecat.registry.actions.schemas import (
    AnnotatedRegistryActionImpl,
    RegistryActionInterface,
    RegistryActionTemplateImpl,
    RegistryActionType,
    RegistryActionUDFImpl,
    TemplateAction,
)


class BoundRegistryAction(BaseModel):
    """Runtime representation of a bound registry action.

    This class holds the actual callable function and metadata for a registry action.
    It's used during action discovery and execution, not for API serialization.
    """

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
    secrets: list[RegistrySecretType] | None = None
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
    requires_approval: bool = False
    required_entitlements: list[str] | None = None

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

    def validate_args(
        self, args: Mapping[str, Any], *, mode: Literal["json", "python"] = "json"
    ) -> dict[str, Any]:
        """Validate the input arguments for a Bound registry action.

        Checks:
        1. The Bound registry action must be called with keyword arguments only.
        2. The input arguments must be validated against the Bound registry action's model.
        """

        # Validate the input arguments, fail early if the input is invalid
        # Note that we've added TemplateValidator to the list of validators
        # so template expressions will pass args model validation
        try:
            # Note that we're allowing type coercion for the input arguments
            # Use cases would be transforming a UTC string to a datetime object
            # We return the validated input arguments as a dictionary
            validated = self.args_cls.model_validate(args)
            validated_args = validated.model_dump(mode=mode)
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
