"""Core Data Transform actions."""
# XXX(WARNING): Do not import __future__ annotations from typing
# This will cause class types to be resolved as strings

from typing import Annotated, Any

from pydantic import Field

# from tracecat.expressions import functions
# from tracecat.expressions.functions import (
#     FunctionConstraint,
#     OperatorConstraint,
#     custom_filter,
# )
from tracecat_registry import registry


@registry.register(
    namespace="core.transform",
    description="Reshapes the input value to the output. You can use this to reshape a JSON-like structure into another easier to manipulate JSON object.",
    default_title="Reshape",
    display_group="Data Transform",
)
def reshape(
    value: Annotated[Any, Field(..., description="The value to reshape")],
) -> Any:
    return value


# @registry.register(
#     namespace="core.transform",
#     description="Filter a collection based on a condition.",
#     default_title="Filter",
#     display_group="Data Transform",
# )
# def filter(
#     items: Annotated[list[Any], Field(..., description="A collection of items.")],
#     constraint: Annotated[
#         str | list[Any] | FunctionConstraint | OperatorConstraint,
#         Field(
#             ...,
#             description=(
#                 "A constraint to filter the collection."
#                 "If a list is provided, it will be used as a set filter."
#                 "If a string is provided, it will be evaluated as a restricted conditional expression."
#                 " Container items are refereneced by `x` e.g. 'x > 2 and x < 6'"
#             ),
#         ),
#     ],
# ) -> Any:
#     return custom_filter(items, constraint)


# @registry.register(
#     namespace="core.transform",
#     description="Build a reference table from a collection of items.",
#     default_title="Build Reference Table",
#     display_group="Data Transform",
# )
# def build_reference_table(
#     items: Annotated[list[Any], Field(..., description="A collection of items.")],
#     key: Annotated[
#         str, Field(..., description="The key to index the reference table.")
#     ],
# ) -> Any:
#     # Key is a jsonpath that references a field
#     # This field will be used as the dict key
#     mapping = {}
#     for item in items:
#         _key = functions.eval_jsonpath(key, operand=item)
#         if not isinstance(_key, int | str):
#             continue
#         mapping[_key] = item
#     return mapping
