import json
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel

from tracecat.expressions.shared import ExprType


class ValidationResult(BaseModel):
    """Base class for validation results."""

    status: Literal["success", "error"]
    msg: str = ""
    detail: Any | None = None
    ref: str | None = None

    def __hash__(self) -> int:
        detail = json.dumps(self.detail, sort_keys=True)
        return hash((self.status, self.msg, detail))


class RegistryValidationResult(ValidationResult):
    """Result of validating a registry action's arguments."""

    validated_args: Mapping[str, Any] | None = None


class ExprValidationResult(ValidationResult):
    """Result of visiting an expression node."""

    expression_type: ExprType


class SecretValidationResult(ValidationResult):
    """Result of validating credentials."""
