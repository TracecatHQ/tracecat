from typing import Annotated, Any

from typing_extensions import Doc

from tracecat_registry import registry


@registry.register(
    default_title="Require Condition",
    description="Check a condition. Raises `AssertionError` if the condition is false. Returns `True` if the condition is true.",
    namespace="core",
)
def require(
    lhs: Annotated[Any, Doc("Left hand side of the condition.")],
    rhs: Annotated[Any, Doc("Right hand side of the condition.")],
    condition: Annotated[
        str,
        Doc(
            "Condition to assert (`==`, `!=`, `>`, `>=`, `<=`, `in`, `not in`, `is`, `is not`)."
        ),
    ],
) -> bool:
    valid_conditions = {
        "==": lambda x, y: x == y,
        "!=": lambda x, y: x != y,
        ">": lambda x, y: x > y,
        ">=": lambda x, y: x >= y,
        "<": lambda x, y: x < y,
        "<=": lambda x, y: x <= y,
        "in": lambda x, y: x in y,
        "not in": lambda x, y: x not in y,
        "is": lambda x, y: x is y,
        "is not": lambda x, y: x is not y,
    }

    if condition not in valid_conditions:
        raise ValueError(
            f"Invalid condition. Expected one of {list(valid_conditions.keys())}, got: {condition}."
        )

    try:
        assert valid_conditions[condition](lhs, rhs)
    except AssertionError as e:
        raise AssertionError(
            f"Expected `{lhs} {condition} {rhs}` to be true, but it was false."
        ) from e
    else:
        return True
