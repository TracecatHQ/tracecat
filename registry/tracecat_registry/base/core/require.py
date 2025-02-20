from typing import Annotated, Any

from typing_extensions import Doc

from tracecat_registry import registry


@registry.register(
    default_title="Require Condition",
    description="Evaluate a conditional expression. Raises `AssertionError` if the condition is false. Returns `True` if the condition is true.",
    namespace="core",
)
def require(
    exprs: Annotated[
        Any | list[Any],
        Doc(
            "Conditional expression(s) to evaluate. All must be true for the result to be true."
        ),
    ],
) -> bool:
    if not isinstance(exprs, list):
        exprs = [exprs]

    if not all(isinstance(expr, bool) for expr in exprs):
        raise ValueError(
            "All expressions must evaluate to a boolean. Got types: "
            + ", ".join(type(expr).__name__ for expr in exprs)
        )

    if not all(exprs):
        raise AssertionError(
            "All expressions must evaluate to true. Got values: "
            + ", ".join(str(expr) for expr in exprs)
        )

    return True
