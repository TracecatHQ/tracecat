from typing import Annotated, Any

from typing_extensions import Doc

from tracecat_registry import registry


@registry.register(
    default_title="Ensure",
    description="Check a condition. Returns `True` if the condition is true, otherwise raises `AssertionError`.",
    namespace="core",
)
def ensure(
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
