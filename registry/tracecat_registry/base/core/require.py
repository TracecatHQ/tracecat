from typing import Annotated

from tracecat.expressions.core import TemplateExpression
from typing_extensions import Doc

from tracecat_registry import registry


@registry.register(
    default_title="Require Condition",
    description="Evaluate a conditional expression. Raises `AssertionError` if the condition is false. Returns `True` if the condition is true.",
    namespace="core",
)
def require(expr: Annotated[str, Doc("Conditional expression to evaluate.")]) -> bool:
    result = TemplateExpression(template=expr).result()
    if not isinstance(result, bool):
        raise ValueError(
            f"Expected `{expr}` to evaluate to a boolean, but it evaluated to `{result}`."
        )
    if not bool(result):
        raise AssertionError(f"Expected `{expr}` to be true, but it was false.")
    return True
