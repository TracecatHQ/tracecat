from typing import Annotated

from typing_extensions import Doc

from tracecat_registry import registry


@registry.register(
    default_title="Require",
    description="Evaluate a conditional expression and returns the boolean result.",
    namespace="core",
)
def require(
    conditions: Annotated[
        bool | list[bool],
        Doc(
            "Conditional expression(s) to evaluate. All must be true for the result to be true."
        ),
    ],
    require_all: Annotated[
        bool,
        Doc(
            "If `True`, all conditions must be true for the result to be true. If `False`, only one condition must be true."
        ),
    ] = True,
    raise_error: Annotated[
        bool,
        Doc(
            "If `True`, the function will raise an error if the condition is false. If `False`, the function will return `False` instead."
        ),
    ] = True,
) -> bool:
    if not isinstance(conditions, list):
        conditions = [conditions]

    if not all(isinstance(condition, bool) for condition in conditions):
        raise ValueError(
            "All conditions must evaluate to a boolean. Got types: "
            + ", ".join(type(condition).__name__ for condition in conditions)
        )

    check = all if require_all else any
    msg = "All" if require_all else "At least one"

    if not check(conditions):
        if raise_error:
            raise AssertionError(
                f"{msg} condition must evaluate to true. Got values: "
                + ", ".join(str(condition) for condition in conditions)
            )
        else:
            return False

    return True
