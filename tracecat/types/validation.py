from typing import Any, Literal

from pydantic import BaseModel

from tracecat.expressions.shared import ExprType


class ValidationResult(BaseModel):
    """Base class for validation results."""

    status: Literal["success", "error"]
    msg: str = ""
    detail: Any | None = None


class RegistryValidationResult(ValidationResult):
    """Result of validating a UDF args."""

    validated_args: dict[str, Any] | None = None


class ExprValidationResult(ValidationResult):
    """Result of visiting an expression node."""

    exprssion_type: ExprType


class SecretValidationResult(ValidationResult):
    """Result of validating credentials."""
