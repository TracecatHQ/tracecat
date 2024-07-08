"""Core Data Transform actions."""
# XXX(WARNING): Do not import __future__ annotations from typing
# This will cause class types to be resolved as strings

from typing import Annotated, Any

from pydantic import Field

from tracecat.expressions.functions import custom_filter
from tracecat.registry import registry


@registry.register(
    namespace="core.transform",
    version="0.1.0",
    description="Forwards the input value to the output. You can use this to reshape a JSON-like structure.",
    default_title="Forward",
    display_group="Data Transform",
)
def forward(
    value: Annotated[Any, Field(..., description="The value to forward")],
) -> Any:
    return value


@registry.register(
    namespace="core.transform",
    version="0.1.0",
    description="Filter a collection based on a condition.",
    default_title="Filter",
    display_group="Data Transform",
)
def filter(
    items: Annotated[list[Any], Field(..., description="A collection of items.")],
    constraint: Annotated[
        str | list[Any],
        Field(
            ...,
            description=(
                "A constraint to filter the collection."
                "If a list is provided, it will be used as a set filter."
                "If a string is provided, it will be evaluated as a restricted conditional expression."
                " Container items are refereneced by `x` e.g. 'x > 2 and x < 6'"
            ),
        ),
    ],
) -> Any:
    return custom_filter(items, constraint)
