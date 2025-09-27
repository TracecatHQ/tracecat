from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import orjson
from pydantic_core import to_jsonable_python
from typing_extensions import Doc

from tracecat.config import TRACECAT__MAX_ROWS_CLIENT_POSTGRES
from tracecat.tables.enums import SqlType
from tracecat.tables.models import (
    TableColumnCreate,
    TableColumnRead,
    TableCreate,
    TableRead,
    TableRowInsert,
)
from tracecat.tables.service import TablesService
from tracecat_registry import registry
from tracecat.expressions.functions import tabulate


@registry.register(
    default_title="Lookup record",
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
        str | list[str],
        Doc("The column(s) to lookup the value in."),
    ],
    value: Annotated[
        Any | list[Any],
        Doc("The value(s) to lookup."),
    ],
) -> dict[str, Any] | None:
    return await lookup_many(table=table, column=column, value=value, limit=1)


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
        str | list[str],
        Doc("The column(s) to lookup the value in."),
    ],
    value: Annotated[
        Any | list[Any],
        Doc("The value(s) to lookup."),
    ],
    limit: Annotated[
        int,
        Doc("The maximum number of rows to return."),
    ] = 100,
) -> list[dict[str, Any]]:
    if limit > TRACECAT__MAX_ROWS_CLIENT_POSTGRES:
        raise ValueError(
            f"Limit cannot be greater than {TRACECAT__MAX_ROWS_CLIENT_POSTGRES}"
        )

    # Check if there is only one value or not
    if not isinstance(column, list):
        column = [column]
    if not isinstance(value, list):
        value = [value]

    if len(column) != len(value):
        raise ValueError(
            "Number of columns and values mismatch. You must set one and only one value for each column."
        )

    async with TablesService.with_session() as service:
        rows = await service.lookup_rows(
            table_name=table,
            columns=column,
            values=value,
            limit=limit,
        )
    return rows


@registry.register(
    default_title="Search records",
    description="Search for records in a table with optional filtering.",
    display_group="Tables",
    namespace="core.table",
)
async def search_records(
    table: Annotated[
        str,
        Doc("The table to search in."),
    ],
    search_term: Annotated[
        str | None,
        Doc("Text to search for across all text and JSONB columns."),
    ] = None,
    start_time: Annotated[
        datetime | None,
        Doc("Filter records created after this time."),
    ] = None,
    end_time: Annotated[
        datetime | None,
        Doc("Filter records created before this time."),
    ] = None,
    updated_before: Annotated[
        datetime | None,
        Doc("Filter records updated before this time."),
    ] = None,
    updated_after: Annotated[
        datetime | None,
        Doc("Filter records updated after this time."),
    ] = None,
    offset: Annotated[
        int,
        Doc("The number of rows to skip."),
    ] = 0,
    limit: Annotated[
        int,
        Doc("The maximum number of rows to return."),
    ] = 100,
) -> list[dict[str, Any]]:
    if limit > TRACECAT__MAX_ROWS_CLIENT_POSTGRES:
        raise ValueError(
            f"Limit cannot be greater than {TRACECAT__MAX_ROWS_CLIENT_POSTGRES}"
        )

    async with TablesService.with_session() as service:
        db_table = await service.get_table_by_name(table)

        rows = await service.search_rows(
            table=db_table,
            search_term=search_term,
            start_time=start_time,
            end_time=end_time,
            updated_before=updated_before,
            updated_after=updated_after,
            limit=limit,
            offset=offset,
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
    upsert: Annotated[
        bool,
        Doc("If true, update the row if it already exists (based on primary key)."),
    ] = False,
) -> Any:
    params = TableRowInsert(data=row_data, upsert=upsert)
    async with TablesService.with_session() as service:
        db_table = await service.get_table_by_name(table)
        row = await service.insert_row(table=db_table, params=params)
    return row


@registry.register(
    default_title="Insert multiple records",
    description="Insert multiple records into a table.",
    display_group="Tables",
    namespace="core.table",
)
async def insert_rows(
    table: Annotated[
        str,
        Doc("The table to insert the rows into."),
    ],
    rows_data: Annotated[
        list[dict[str, Any]],
        Doc("The list of data to insert into the table."),
    ],
    upsert: Annotated[
        bool,
        Doc("If true, update the rows if they already exist (based on primary key)."),
    ] = False,
) -> int:
    async with TablesService.with_session() as service:
        db_table = await service.get_table_by_name(table)
        count = await service.batch_insert_rows(
            table=db_table, rows=rows_data, upsert=upsert
        )
    return count


@registry.register(
    default_title="Update record",
    description="Update a record in a table.",
    display_group="Tables",
    namespace="core.table",
)
async def update_row(
    table: Annotated[
        str,
        Doc("The table to update the row in."),
    ],
    row_id: Annotated[
        str,
        Doc("The ID of the row to update."),
    ],
    row_data: Annotated[
        dict[str, Any],
        Doc("The new data for the row."),
    ],
) -> Any:
    async with TablesService.with_session() as service:
        db_table = await service.get_table_by_name(table)
        row = await service.update_row(
            table=db_table, row_id=UUID(row_id), data=row_data
        )
    return row


@registry.register(
    default_title="Delete record",
    description="Delete a record from a table.",
    display_group="Tables",
    namespace="core.table",
)
async def delete_row(
    table: Annotated[
        str,
        Doc("The table to delete the row from."),
    ],
    row_id: Annotated[
        str,
        Doc("The ID of the row to delete."),
    ],
) -> None:
    async with TablesService.with_session() as service:
        db_table = await service.get_table_by_name(table)
        await service.delete_row(table=db_table, row_id=UUID(row_id))


@registry.register(
    default_title="Create table",
    description="Create a new lookup table with optional columns.",
    display_group="Tables",
    namespace="core.table",
)
async def create_table(
    name: Annotated[
        str,
        Doc("The name of the table to create."),
    ],
    columns: Annotated[
        list[dict[str, Any]] | None,
        Doc(
            "List of column definitions. Each column should have 'name', 'type', and optionally 'nullable' and 'default' fields."
        ),
    ] = None,
) -> dict[str, Any]:
    column_objects = []
    if columns:
        for col in columns:
            column_objects.append(
                TableColumnCreate(
                    name=col["name"],
                    type=SqlType(col["type"]),
                    nullable=col.get("nullable", True),
                    default=col.get("default"),
                )
            )

    params = TableCreate(name=name, columns=column_objects)
    async with TablesService.with_session() as service:
        table = await service.create_table(params)
    return table.model_dump()


@registry.register(
    default_title="List tables",
    description="Get a list of all available tables in the workspace.",
    display_group="Tables",
    namespace="core.table",
)
async def list_tables() -> list[dict[str, Any]]:
    async with TablesService.with_session() as service:
        tables = await service.list_tables()
    return [table.model_dump() for table in tables]


@registry.register(
    default_title="Get table metadata",
    description="Get a table's metadata by name. This includes the columns and whether they are indexed.",
    display_group="Tables",
    namespace="core.table",
)
async def get_table_metadata(
    name: Annotated[str, Doc("The name of the table to get.")],
) -> dict[str, Any]:
    async with TablesService.with_session() as service:
        table = await service.get_table_by_name(name)

        # Get unique index info or default to empty dict if not present
        index_columns = await service.get_index(table)

        # Convert to response model (includes is_index field)
        res = TableRead(
            id=table.id,
            name=table.name,
            columns=[
                TableColumnRead(
                    id=column.id,
                    name=column.name,
                    type=SqlType(column.type),
                    nullable=column.nullable,
                    default=column.default,
                    is_index=column.name in index_columns,
                )
                for column in table.columns
            ],
        )
    return res.model_dump(mode="json")


@registry.register(
    default_title="Download table data",
    description="Download a table's data by name as list of dicts, JSON string, NDJSON string, CSV or Markdown.",
    display_group="Tables",
    namespace="core.table",
)
async def download(
    name: Annotated[str, Doc("The name of the table to download.")],
    format: Annotated[
        Literal["json", "ndjson", "csv", "markdown"] | None,
        Doc("The format to download the table data in."),
    ] = None,
    limit: Annotated[int, Doc("The maximum number of rows to download.")] = 1000,
) -> list[dict[str, Any]] | str:
    if limit > 1000:
        raise ValueError("Cannot return more than 1000 rows")

    async with TablesService.with_session() as service:
        table = await service.get_table_by_name(name)
        rows = await service.list_rows(table=table, limit=limit)

        # Convert rows to JSON-safe format (handles UUID and other non-serializable types)
        json_safe_rows = to_jsonable_python(rows, fallback=str)

        if format is None:
            return json_safe_rows
        elif format == "json":
            return orjson.dumps(json_safe_rows).decode()
        elif format == "ndjson":
            return "\n".join([orjson.dumps(row).decode() for row in json_safe_rows])
        elif format in ["csv", "markdown"]:
            return tabulate(json_safe_rows, format)
        return tabulate(json_safe_rows, format)
