from collections.abc import Mapping
from typing import Annotated, Any, TypeVar

from pydantic import ValidationInfo, ValidatorFunctionWrapHandler
from pydantic.functional_validators import WrapValidator

from tracecat.common import is_iterable
from tracecat.expressions.patterns import FULL_TEMPLATE


def is_full_template(template: str) -> bool:
    """Check if a string is a complete template expression (${{...}})"""
    return FULL_TEMPLATE.match(template) is not None


T = TypeVar("T")


# We can bundle validators and unpack them in a single expression
class TemplateValidator:
    def __new__(cls):
        return WrapValidator(cls.maybe_templated_expression)

    @classmethod
    def maybe_templated_expression(
        cls, v: Any, handler: ValidatorFunctionWrapHandler, info: ValidationInfo
    ) -> Any:
        try:
            # Quick win for simple expressions
            return handler(v)
        except Exception:
            # Fallback to recursive validation for template expressions
            return recursive_validator(v, handler)


def recursive_validator(v: Any, handler: ValidatorFunctionWrapHandler) -> Any:
    """Allows for templated expressions in the input data.

    This validator is used to validate the input data for templated expressions.
    It will skip validation for template expressions and only validate the input data for the expected type.
    It allows expressions to exist at any level of the input data, including the top level.

    e.g.
    ```python
    class Test(BaseModel):
        a: Annotated[dict[str, list[int]], TemplateValidator()]

    print(Test(a={"b": "${{ my_list }}"}).model_dump())
    ```
    """
    # If the input value is a string and a full template,
    # accept it regardless of the expected type
    if isinstance(v, str) and is_full_template(v):
        # skip validation for template expressions
        return v

    # Handle nested mappings by recursively validating values
    if isinstance(v, Mapping):
        return type(v)(
            **{key: recursive_validator(value, handler) for key, value in v.items()}
        )

    # Handle nested iterables (lists, tuples, etc.)
    if is_iterable(v, container_only=True):
        return type(v)(recursive_validator(item, handler) for item in v)

    return handler(v)


class RequiredTemplateValidator:
    def __new__(cls):
        return WrapValidator(cls.must_expression)

    @classmethod
    def must_expression(cls, v: T, handler: ValidatorFunctionWrapHandler, info) -> T:
        if isinstance(v, str) and not is_full_template(v):
            raise ValueError(f"'{v}' is not a valid expression")
        return handler(v, info)


ExpressionStr = Annotated[str, TemplateValidator()]
RequiredExpressionStr = Annotated[str, RequiredTemplateValidator()]
