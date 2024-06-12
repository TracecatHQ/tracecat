"""Core Data Transform actions."""
# XXX(WARNING): Do not import __future__ annotations from typing
# This will cause class types to be resolved as strings

from typing import Annotated, Any

from pydantic import Field

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
