"""Core Data Transform actions."""
# XXX(WARNING): Do not import __future__ annotations from typing
# This will cause class types to be resolved as strings

from typing import Annotated, Any

from tracecat.expressions import functions
from typing_extensions import Doc

from tracecat_registry import registry


@registry.register(
    namespace="core.transform",
    description="Reshapes the input value to the output. You can use this to reshape a JSON-like structure into another easier to manipulate JSON object.",
    default_title="Reshape",
    display_group="Data Transform",
)
def reshape(
    value: Annotated[
        Any,
        Doc("The value to reshape"),
    ],
) -> Any:
    return value


@registry.register(
    namespace="core.transform",
    description="Filter a collection based on a condition.",
    default_title="Filter",
    display_group="Data Transform",
)
def filter(
    items: Annotated[
        list[Any],
        Doc("A collection of items."),
    ],
    python_lambda: Annotated[
        str,
        Doc("A Python lambda function for filtering the collection."),
    ],
) -> Any:
    return functions.filter_(items=items, python_lambda=python_lambda)
