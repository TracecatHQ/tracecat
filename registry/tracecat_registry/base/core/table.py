from typing import Annotated, Any

from typing_extensions import Doc

from tracecat_registry import ActionIsInterfaceError, registry


@registry.register(
    default_title="Lookup Table",
    description="Get a row from a table corresponding to the given column and value.",
    display_group="Tables",
    namespace="core.table",
)
async def lookup(
    table: Annotated[
        str,
        Doc("The table to lookup the value in."),
    ],
    column: Annotated[
        str,
        Doc("The column to lookup the value in."),
    ],
    value: Annotated[
        str,
        Doc("The value to lookup."),
    ],
) -> Any:
    raise ActionIsInterfaceError
