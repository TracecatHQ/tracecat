from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

import orjson
from pydantic import BaseModel, ValidationError
from pydantic_core import to_jsonable_python

from tracecat.expressions.common import ExprType


@dataclass(slots=True)
class ValidationDetail:
    """Detail of a validation result."""

    type: str
    msg: str
    loc: tuple[int | str, ...] | None = None

    @classmethod
    def list_from_pydantic(cls, err: ValidationError) -> list[ValidationDetail]:
        return [
            cls(type=f"pydantic.{err['type']}", msg=err["msg"], loc=err["loc"])
            for err in err.errors(include_input=False, include_url=False)
        ]


class ValidationResult(BaseModel):
    """Base class for validation results."""

    status: Literal["success", "error"]
    msg: str = ""
    detail: list[ValidationDetail] | None = None
    ref: str | None = None

    def __hash__(self) -> int:
        detail = orjson.dumps(
            self.detail, default=to_jsonable_python, option=orjson.OPT_SORT_KEYS
        )
        return hash((self.status, self.msg, detail))


class RegistryValidationResult(ValidationResult):
    """Result of validating a registry action's arguments."""

    validated_args: Mapping[str, Any] | None = None


class ExprValidationResult(ValidationResult):
    """Result of visiting an expression node."""

    expression_type: ExprType


class TemplateActionExprValidationResult(ExprValidationResult):
    """Result of visiting an expression node."""

    loc: str


class SecretValidationDetail(TypedDict):
    """Detail of a secret validation result."""

    environment: str
    secret_name: str


class SecretValidationResult(ValidationResult):
    """Result of validating credentials."""

    detail: SecretValidationDetail | None = None
