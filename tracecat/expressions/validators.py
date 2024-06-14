from typing import Any, TypeVar

from pydantic import ValidationInfo, ValidatorFunctionWrapHandler
from pydantic.functional_validators import WrapValidator

from tracecat.expressions.patterns import FULL_TEMPLATE


def is_full_template(template: str) -> bool:
    # return template.startswith("${{") and template.endswith("}}")
    return FULL_TEMPLATE.match(template) is not None


def is_full_template_string(template: Any) -> bool:
    return isinstance(template, str) and is_full_template(template)


T = TypeVar("T")


def is_iterable(obj):
    try:
        iter(obj)
    except TypeError:
        return False
    else:
        return True


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
        if is_full_template_string(v):
            # if its a string and a full template, return it as is
            return v

        # # Otherwise, it's an inline template or non-template
        # # Call the default handler
        # elif isinstance(v, Mapping):
        #     return {
        #         k: cls.maybe_templated_expression(vv, handler, info)
        #         for k, vv in v.items()
        #     }
        # elif is_iterable(v) and not isinstance(v, str):
        #     return type(v)(
        #         cls.maybe_templated_expression(vv, handler, info) for vv in v
        #     )
        else:
            print("Validating", v, str(info))
            res = handler(v, info)
            return res
