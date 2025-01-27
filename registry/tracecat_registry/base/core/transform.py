"""Core Data Transform actions."""
# XXX(WARNING): Do not import __future__ annotations from typing
# This will cause class types to be resolved as strings

from typing import Annotated, Any

from tracecat.expressions import functions
from typing_extensions import Doc

from tracecat_registry import registry


@registry.register(
    description="Reshapes the input value to the output. You can use this to reshape a JSON-like structure into another easier to manipulate JSON object.",
    default_title="Reshape",
    display_group="Data Transform",
    namespace="core.transform",
)
def reshape(
    value: Annotated[
        Any,
        Doc("The value to reshape"),
    ],
) -> Any:
    return value


@registry.register(
    description="Filter a collection based on a condition.",
    default_title="Filter",
    display_group="Data Transform",
    namespace="core.transform",
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


@registry.register(
    default_title="Merge JSON objects",
    description="Merge two JSON objects into a single JSON object.",
    display_group="Data Transform",
    namespace="core.transform",
)
def merge(
    left: Annotated[dict[str, Any], Field(..., description="Left JSON object")],
    right: Annotated[dict[str, Any], Field(..., description="Right JSON object")],
) -> dict[str, Any]:
    """Merge two JSON objects into a single JSON object."""
    return {**left, **right}
