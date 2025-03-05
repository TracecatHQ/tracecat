from typing import Annotated, Any

from typing_extensions import Doc

from tracecat_registry import registry


@registry.register(
    default_title="Require",
    description="Evaluate a conditional expression. Raises `AssertionError` if the condition is false. Returns `True` if the condition is true.",
    namespace="core",
)
def require(
    conditions: Annotated[
        Any | list[Any],
        Doc(
            "Conditional expression(s) to evaluate. All must be true for the result to be true."
        ),
    ],
) -> bool:
    if not isinstance(conditions, list):
        conditions = [conditions]

    if not all(isinstance(condition, bool) for condition in conditions):
        raise ValueError(
            "All conditions must evaluate to a boolean. Got types: "
            + ", ".join(type(condition).__name__ for condition in conditions)
        )

    if not all(conditions):
        raise AssertionError(
            "All conditions must evaluate to true. Got values: "
            + ", ".join(str(condition) for condition in conditions)
        )

    return True
