from typing import Annotated, Any

from typing_extensions import Doc


from tracecat.tables.models import TableRowInsert
from tracecat.tables.service import TablesService
from tracecat_registry import registry


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
        Any,
        Doc("The value to lookup."),
    ],
) -> Any:
    async with TablesService.with_session() as service:
        rows = await service.lookup_row(
            table_name=table,
            columns=[column],
            values=[value],
        )
    return rows[0] if rows else None


@registry.register(
    default_title="Insert Row",
    description="Insert a row into a table.",
    display_group="Tables",
    namespace="core.table",
)
async def insert_row(
    table: Annotated[
        str,
        Doc("The table to insert the row into."),
    ],
    row_data: Annotated[
        dict[str, Any],
        Doc("The data to insert into the row."),
    ],
) -> Any:
    params = TableRowInsert(data=row_data)
    async with TablesService.with_session() as service:
        db_table = await service.get_table_by_name(table)
        row = await service.insert_row(table=db_table, params=params)
    return row
