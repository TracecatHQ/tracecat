from collections.abc import Mapping

from pydantic import ValidationInfo, ValidatorFunctionWrapHandler
from pydantic.functional_validators import WrapValidator

from tracecat.expressions.patterns import FULL_TEMPLATE


def is_full_template(template: str) -> bool:
    # return template.startswith("${{") and template.endswith("}}")
    return FULL_TEMPLATE.match(template) is not None


def is_iterable(value: object, *, container_only: bool = True) -> bool:
    """Check if a value is iterable, optionally excluding string-like and mapping types.

    Args:
        value: The value to check for iterability
        container_only: If True, excludes strings and bytes objects from being considered iterable

    Returns:
        bool: True if the value is iterable (according to the specified rules), False otherwise
    """
    if isinstance(value, str | bytes):
        return not container_only
    if isinstance(value, Mapping):
        return False
    return hasattr(value, "__iter__")


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
