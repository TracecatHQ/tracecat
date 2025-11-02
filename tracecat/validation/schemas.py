from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, TypedDict

import orjson
from pydantic import BaseModel, Field, RootModel
from pydantic import ValidationError as PydanticValidationError
from pydantic_core import to_jsonable_python

from tracecat.expressions.common import ExprType


@dataclass(slots=True)
class ValidationDetail:
    """Detail of a validation result."""

    type: str
    msg: str
    loc: tuple[int | str, ...] | None = None

    def __hash__(self) -> int:
        return hash((self.type, self.msg, self.loc))

    @classmethod
    def list_from_pydantic(cls, err: PydanticValidationError) -> list[ValidationDetail]:
        return [
            cls(type=f"pydantic.{err['type']}", msg=err["msg"], loc=err["loc"])
            for err in err.errors(include_input=False, include_url=False)
        ]


class ValidationResultType(StrEnum):
    """Type of a validation error."""

    DSL = "dsl"
    SECRET = "secret"
    EXPRESSION = "expression"
    ACTION = "action"
    ACTION_TEMPLATE = "action_template"


class BaseValidationResult(BaseModel):
    """Base class for validation results."""

    type: ValidationResultType
    status: Literal["success", "error"]
    msg: str = ""
    detail: list[ValidationDetail] | None = None
    ref: str | None = None

    def __hash__(self) -> int:
        detail = orjson.dumps(
            self.detail, default=to_jsonable_python, option=orjson.OPT_SORT_KEYS
        )
        return hash((self.status, self.msg, detail))


class DSLValidationResult(BaseValidationResult):
    """Result of validating a generic input."""

    type: Literal[ValidationResultType.DSL] = ValidationResultType.DSL


class ActionValidationResult(BaseValidationResult):
    """Result of validating a registry action's arguments."""

    type: Literal[ValidationResultType.ACTION] = ValidationResultType.ACTION
    action_type: str
    validated_args: Mapping[str, Any] | None = None


class ExprValidationResult(BaseValidationResult):
    """Result of visiting an expression node."""

    type: Literal[ValidationResultType.EXPRESSION] = ValidationResultType.EXPRESSION
    expression: str | None = None
    expression_type: ExprType


class TemplateActionExprValidationResult(ExprValidationResult):
    """Result of visiting an expression node."""

    type: Literal[ValidationResultType.ACTION_TEMPLATE] = (
        ValidationResultType.ACTION_TEMPLATE
    )
    loc: tuple[str | int, ...]


class SecretValidationResult(BaseValidationResult):
    """Result of validating credentials."""

    type: Literal[ValidationResultType.SECRET] = ValidationResultType.SECRET
    detail: SecretValidationDetail | None = None


class SecretValidationDetail(TypedDict):
    """Detail of a secret validation result."""

    environment: str
    secret_name: str


ValidationResultVariant = (
    DSLValidationResult
    | SecretValidationResult
    | ExprValidationResult
    | TemplateActionExprValidationResult
    | ActionValidationResult
)


class ValidationResult(RootModel):
    root: ValidationResultVariant = Field(discriminator="type")

    def __hash__(self) -> int:
        return hash(self.root)

    @classmethod
    def new(
        cls,
        result: ValidationResultVariant | None = None,
        **kwargs: Any,
    ) -> ValidationResult:
        if len(kwargs) > 0:
            match type_ := kwargs.get("type"):
                case ValidationResultType.DSL:
                    return cls(root=DSLValidationResult(**kwargs))
                case ValidationResultType.SECRET:
                    return cls(root=SecretValidationResult(**kwargs))
                case ValidationResultType.EXPRESSION:
                    return cls(root=ExprValidationResult(**kwargs))
                case ValidationResultType.ACTION_TEMPLATE:
                    return cls(root=TemplateActionExprValidationResult(**kwargs))
                case ValidationResultType.ACTION:
                    return cls(root=ActionValidationResult(**kwargs))
                case _:
                    raise ValueError(f"Invalid root type: {type_}")
        elif result is not None:
            return cls(root=result)
        else:
            raise ValueError("No concrete or kwargs provided")
