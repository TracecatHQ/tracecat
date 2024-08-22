import json
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel

from tracecat.expressions.shared import ExprType


class ValidationResult(BaseModel):
    """Base class for validation results."""

    status: Literal["success", "error"]
    msg: str = ""
    detail: Any | None = None

    def __hash__(self) -> int:
        detail = json.dumps(self.detail, sort_keys=True)
        return hash((self.status, self.msg, detail))


class RegistryValidationResult(ValidationResult):
    """Result of validating a UDF args."""

    validated_args: dict[str, Any] | None = None


class ExprValidationResult(ValidationResult):
    """Result of visiting an expression node."""

    expression_type: ExprType


class SecretValidationResult(ValidationResult):
    """Result of validating credentials."""


VALIDATION_TYPES = {
    "duration": timedelta,
    "datetime": datetime,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "any": Any,
}
