from datetime import datetime
from typing import Annotated, Any, Literal

from typing_extensions import Doc

from tracecat_registry import config, registry, types
from tracecat_registry.context import get_context
from tracecat_registry.sdk.exceptions import TracecatConflictError


@registry.register(
    default_title="Lookup row",
    description="Get a single row from a table corresponding to the given column and value.",
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
    return await get_context().tables.lookup(table=table, column=column, value=value)


@registry.register(
    default_title="Is in table",
    description="Check if a value exists in a table column.",
    display_group="Tables",
    namespace="core.table",
)
async def is_in(
    table: Annotated[
        str,
        Doc("The table to check."),
    ],
    column: Annotated[
        str,
        Doc("The column to check in."),
    ],
    value: Annotated[
        Any,
        Doc("The value to check for."),
    ],
) -> bool:
    return await get_context().tables.exists(table=table, column=column, value=value)


@registry.register(
    default_title="Lookup many rows",
    description="Get multiple rows from a table corresponding to the given column and values.",
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
    if limit > config.MAX_ROWS_CLIENT_POSTGRES:
        raise ValueError(
            f"Limit cannot be greater than {config.MAX_ROWS_CLIENT_POSTGRES}"
        )

    params: dict[str, Any] = {
        "table": table,
        "column": column,
        "value": value,
    }
    if limit is not None:
        params["limit"] = limit
    return await get_context().tables.lookup_many(**params)


@registry.register(
    default_title="Search rows",
    description="Search for rows in a table with optional filtering.",
    display_group="Tables",
    namespace="core.table",
)
async def search_rows(
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
        Doc("Filter rows created after this time."),
    ] = None,
    end_time: Annotated[
        datetime | None,
        Doc("Filter rows created before this time."),
    ] = None,
    updated_before: Annotated[
        datetime | None,
        Doc("Filter rows updated before this time."),
    ] = None,
    updated_after: Annotated[
        datetime | None,
        Doc("Filter rows updated after this time."),
    ] = None,
    cursor: Annotated[
        str | None,
        Doc("Cursor for pagination."),
    ] = None,
    reverse: Annotated[
        bool,
        Doc("Reverse pagination direction."),
    ] = False,
    limit: Annotated[
        int,
        Doc("The maximum number of rows to return."),
    ] = 100,
) -> types.TableSearchResponse:
    if limit > config.MAX_ROWS_CLIENT_POSTGRES:
        raise ValueError(
            f"Limit cannot be greater than {config.MAX_ROWS_CLIENT_POSTGRES}"
        )

    params: dict[str, Any] = {"table": table}
    if search_term is not None:
        params["search_term"] = search_term
    if start_time is not None:
        params["start_time"] = start_time
    if end_time is not None:
        params["end_time"] = end_time
    if updated_before is not None:
        params["updated_before"] = updated_before
    if updated_after is not None:
        params["updated_after"] = updated_after
    if limit is not None:
        params["limit"] = limit
    if cursor is not None:
        params["cursor"] = cursor
    params["reverse"] = reverse
    return await get_context().tables.search_rows(**params)


@registry.register(
    default_title="Insert row",
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
    upsert: Annotated[
        bool,
        Doc("If true, update the row if it already exists (based on primary key)."),
    ] = False,
) -> dict[str, Any]:
    return await get_context().tables.insert_row(
        table=table,
        row_data=row_data,
        upsert=upsert,
    )


@registry.register(
    default_title="Insert multiple rows",
    description="Insert multiple rows into a table.",
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
    return await get_context().tables.insert_rows(
        table=table,
        rows_data=rows_data,
        upsert=upsert,
    )


@registry.register(
    default_title="Update row",
    description="Update a row in a table.",
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
) -> dict[str, Any]:
    return await get_context().tables.update_row(
        table=table,
        row_id=row_id,
        row_data=row_data,
    )


@registry.register(
    default_title="Delete row",
    description="Delete a row from a table.",
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
    await get_context().tables.delete_row(table=table, row_id=row_id)


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
    raise_on_duplicate: Annotated[
        bool,
        Doc("If true, raise an error if the table already exists."),
    ] = True,
) -> types.Table:
    client_params: dict[str, Any] = {
        "name": name,
        "raise_on_duplicate": raise_on_duplicate,
    }
    if columns is not None:
        client_params["columns"] = columns
    try:
        return await get_context().tables.create_table(**client_params)
    except TracecatConflictError as exc:
        raise ValueError("Table already exists") from exc


@registry.register(
    default_title="List tables",
    description="Get a list of all available tables in the workspace.",
    display_group="Tables",
    namespace="core.table",
)
async def list_tables() -> list[types.Table]:
    return await get_context().tables.list_tables()


@registry.register(
    default_title="Get table metadata",
    description="Get a table's metadata by name. This includes the columns and whether they are indexed.",
    display_group="Tables",
    namespace="core.table",
)
async def get_table_metadata(
    name: Annotated[str, Doc("The name of the table to get.")],
) -> types.TableRead:
    return await get_context().tables.get_table_metadata(name)


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

    params: dict[str, Any] = {"table": name}
    if format is not None:
        params["format"] = format
    if limit is not None:
        params["limit"] = limit
    return await get_context().tables.download(**params)
