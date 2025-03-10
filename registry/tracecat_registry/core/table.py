from typing import Annotated, Any

from typing_extensions import Doc


from tracecat.tables.models import TableRowInsert
from tracecat.tables.service import TablesService
from tracecat_registry import registry


@registry.register(
    default_title="Lookup one record",
    description="Get a single record from a table corresponding to the given column and value.",
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
) -> dict[str, Any] | None:
    async with TablesService.with_session() as service:
        rows = await service.lookup_rows(
            table_name=table,
            columns=[column],
            values=[value],
            limit=1,
        )
    # Since we set limit=1, we know there will be at most one row
    return rows[0] if rows else None


@registry.register(
    default_title="Lookup many records",
    description="Get multiple records from a table corresponding to the given column and values.",
    display_group="Tables",
    namespace="core.table",
)
async def lookup_many(
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
    limit: Annotated[
        int,
        Doc("The maximum number of rows to return."),
    ] = 100,
) -> list[dict[str, Any]]:
    async with TablesService.with_session() as service:
        rows = await service.lookup_rows(
            table_name=table,
            columns=[column],
            values=[value],
            limit=limit,
        )
    return rows


@registry.register(
    default_title="Insert record",
    description="Insert a record into a table.",
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
