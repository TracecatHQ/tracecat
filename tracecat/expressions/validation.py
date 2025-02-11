from typing import Annotated, Any, TypeVar

from pydantic import ValidationInfo, ValidatorFunctionWrapHandler
from pydantic.functional_validators import WrapValidator

from tracecat.expressions.patterns import FULL_TEMPLATE


def is_full_template(template: str) -> bool:
    # return template.startswith("${{") and template.endswith("}}")
    return FULL_TEMPLATE.match(template) is not None


def is_iterable(value: Any, *, container_only: bool = True) -> bool:
    try:
        iter(value)
        if isinstance(value, dict):
            # We don't consider dictionaries as iterables
            return False
        if container_only:
            return not isinstance(value, str | bytes)
        return True
    except TypeError:
        return False


T = TypeVar("T")


# We can bundle validators and unpack them in a single expression
class TemplateValidator:
    def __new__(cls):
        return WrapValidator(cls.maybe_templated_expression)

    @classmethod
    def maybe_templated_expression(
        cls, v: T, handler: ValidatorFunctionWrapHandler, info: ValidationInfo
    ) -> T:
        # If the input value is a string and a full template,
        # v0: We don't care about the coercion type and just return the string value
        # i.e., we defer the type checking to runtime
        if isinstance(v, str) and is_full_template(v):
            # if its a string and a full template, return it as is
            return v
        # Otherwise, it's an inline template or non-template
        # Call the default handler
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
