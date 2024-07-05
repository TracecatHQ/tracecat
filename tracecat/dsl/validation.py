from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from tracecat.logging import logger
from tracecat.types.exceptions import TracecatValidationError
from tracecat.types.validation import VALIDATION_TYPES, ValidationResult

if TYPE_CHECKING:
    from tracecat.dsl.common import DSLInput

LIST_PATTERN = re.compile(r"list\[(?P<inner>(\$)?[a-zA-Z]+)\]")


class SchemaValidatorFactory:
    """Factory for generating Pydantic models from a user-defined schema."""

    def __init__(self, schema: dict[str, Any], *, raise_exceptions: bool = False):
        if not isinstance(schema, dict):
            raise TypeError("Schema must be a dict")

        _schema = schema.copy()  # XXX: Copy the schema to prevent mutation
        self.refs: dict[str, Any] = _schema.pop("$refs", {})
        self.schema = _schema
        self._raise_exceptions = raise_exceptions
        self._errors = []

    def __repr__(self):
        return f"SchemaValidatorFactory({self.schema})"

    def create(self, raise_exceptions: bool = True):
        validator = self._generate_model_from_schema(
            self.schema, "TriggerInputValidator"
        )
        if self._errors and raise_exceptions:
            raise ExceptionGroup(
                "SchemaValidatorFactory failed to create validator", self._errors
            )
        return validator

    def errors(self):
        return self._errors

    def _generate_model_from_schema(
        self, schema: dict[str, Any], model_name: str
    ) -> type[BaseModel]:
        """Generate a Pydantic model from a schema (dict)."""
        fields = {}
        for field_name, field_type in schema.items():
            field_info = Field(default=...)  # Required
            fields[field_name] = (
                self._resolve_type(field_name, field_type),
                field_info,
            )
        return create_model(
            model_name,
            __config__=ConfigDict(extra="forbid", arbitrary_types_allowed=True),
            **fields,
        )

    def _return_or_raise(self, msg: str, detail: Any | None = None) -> type:
        exc = TracecatValidationError(msg, detail=detail)
        if self._raise_exceptions:
            raise exc
        self._errors.append(exc)
        return object  # Return a dummy object type

    def _resolve_type(self, field_name: str, field_type: Any) -> type:
        """Takes a field type and evaluates it to a type annotation."""
        if isinstance(field_type, str):
            return self._resolve_string_type(field_name, field_type)
        elif isinstance(field_type, dict):
            return self._generate_model_from_schema(field_type, field_name.capitalize())
        elif isinstance(field_type, list):
            return self._return_or_raise("Specify lists with list[T] syntax")
        return self._return_or_raise(
            f"Invalid type {field_type!r}", detail=f"Check field {field_name!r}"
        )

    def _resolve_string_type(self, field_name: str, typename: str) -> type:
        if typename in VALIDATION_TYPES:
            return VALIDATION_TYPES[typename]
        if typename == "list":
            return list[Any]
        if typename[0] == "$":
            ref_name = typename.lstrip("$")
            if ref_schema := self.refs.get(ref_name):
                return self._generate_model_from_schema(
                    ref_schema, f"Ref{ref_name.capitalize()}"
                )
            return self._return_or_raise(
                f"Reference type {ref_name!r} not found in $refs"
                f"Check field {field_name!r}. $refs: {self.refs}",
            )

        # list[inner]
        if match := LIST_PATTERN.match(typename):
            # Case 1: inner is a reference type
            inner = match.group("inner")
            resolved_inner = self._resolve_string_type(field_name, inner)
            # Wrap the inner type in a list
            # inner type can be a builtin or a reference type
            return list[resolved_inner]
        return self._return_or_raise(
            f"Invalid type {typename!r}", detail=f"Check field {field_name!r}"
        )


def validate_trigger_inputs(
    dsl: DSLInput, payload: dict[str, Any] | None = None
) -> ValidationResult:
    if dsl.entrypoint.expects is None:
        # If there's no expected trigger input schema, we don't validate it
        # as its ignored anyways
        return ValidationResult(
            status="success", msg="No trigger input schema, skipping validation."
        )
    logger.trace("DSL entrypoint expects", expects=dsl.entrypoint.expects)
    validator_factory = SchemaValidatorFactory(dsl.entrypoint.expects)

    TriggerInputsValidator = validator_factory.create(raise_exceptions=False)
    if validator_creation_errors := validator_factory.errors():
        logger.error(validator_creation_errors)
        return ValidationResult(
            status="error",
            msg="Error creating trigger input schema validator",
            detail=[str(e) for e in validator_creation_errors],
        )

    if payload is None:
        return ValidationResult(
            status="error",
            msg="Trigger input schema is defined but no payload was provided.",
            detail={"schema": TriggerInputsValidator.model_json_schema()},
        )

    try:
        TriggerInputsValidator.model_validate(payload)
        return ValidationResult(status="success", msg="Trigger inputs are valid.")
    except ValidationError as e:
        return ValidationResult(
            status="error",
            msg=f"Validation error in trigger inputs ({e.title}). Please refer to the schema for more details.",
            detail={
                "errors": e.errors(),
                "schema": TriggerInputsValidator.model_json_schema(),
            },
        )
