from datetime import datetime
from typing import Annotated, Any, Literal

import orjson
from pydantic_core import to_jsonable_python
from typing_extensions import Doc

from tracecat_registry import registry
from tracecat_registry.context import get_context
from tracecat_registry.sdk import TracecatConflictError
from tracecat_registry.utils.datetime import coerce_optional_to_utc_datetime
from tracecat_registry.utils.formatters import tabulate

# Maximum rows that can be returned in a single query
MAX_ROWS_LIMIT = 1000


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
    ctx = get_context()
    rows = await ctx.tables.lookup(
        table_name=table,
        column=column,
        value=value,
        limit=1,
    )
    # Since we set limit=1, we know there will be at most one row
    return rows[0] if rows else None


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
    ctx = get_context()
    return await ctx.tables.exists(
        table_name=table,
        column=column,
        value=value,
    )


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
    if limit > MAX_ROWS_LIMIT:
        raise ValueError(f"Limit cannot be greater than {MAX_ROWS_LIMIT}")

    ctx = get_context()
    return await ctx.tables.lookup(
        table_name=table,
        column=column,
        value=value,
        limit=limit,
    )


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
    offset: Annotated[
        int,
        Doc("The number of rows to skip."),
    ] = 0,
    limit: Annotated[
        int,
        Doc("The maximum number of rows to return."),
    ] = 100,
) -> list[dict[str, Any]]:
    if limit > MAX_ROWS_LIMIT:
        raise ValueError(f"Limit cannot be greater than {MAX_ROWS_LIMIT}")

    ctx = get_context()
    db_table = await ctx.tables.get_table_by_name(table)
    table_id = str(db_table["id"])

    # Build search params - only include non-None values
    kwargs: dict[str, Any] = {
        "offset": offset,
        "limit": limit,
    }
    if search_term is not None:
        kwargs["search_term"] = search_term
    if start_time is not None:
        kwargs["start_time"] = coerce_optional_to_utc_datetime(start_time)
    if end_time is not None:
        kwargs["end_time"] = coerce_optional_to_utc_datetime(end_time)
    if updated_before is not None:
        kwargs["updated_before"] = coerce_optional_to_utc_datetime(updated_before)
    if updated_after is not None:
        kwargs["updated_after"] = coerce_optional_to_utc_datetime(updated_after)

    return await ctx.tables.search_rows(table_id, **kwargs)


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
) -> Any:
    ctx = get_context()
    db_table = await ctx.tables.get_table_by_name(table)
    table_id = str(db_table["id"])

    return await ctx.tables.insert_row(table_id, data=row_data, upsert=upsert)


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
    ctx = get_context()
    db_table = await ctx.tables.get_table_by_name(table)
    table_id = str(db_table["id"])

    result = await ctx.tables.batch_insert_rows(table_id, rows=rows_data, upsert=upsert)
    return result.get("rows_inserted", 0)


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
) -> Any:
    ctx = get_context()
    db_table = await ctx.tables.get_table_by_name(table)
    table_id = str(db_table["id"])

    return await ctx.tables.update_row(table_id, row_id, data=row_data)


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
    ctx = get_context()
    db_table = await ctx.tables.get_table_by_name(table)
    table_id = str(db_table["id"])

    await ctx.tables.delete_row(table_id, row_id)


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
) -> dict[str, Any]:
    ctx = get_context()

    # Prepare column definitions
    column_objects = []
    if columns:
        for col in columns:
            column_objects.append(
                {
                    "name": col["name"],
                    "type": col["type"],
                    "nullable": col.get("nullable", True),
                    "default": col.get("default"),
                }
            )

    try:
        # Only pass columns if there are any; otherwise let it default to UNSET
        if column_objects:
            await ctx.tables.create_table(name=name, columns=column_objects)
        else:
            await ctx.tables.create_table(name=name)
    except TracecatConflictError:
        if raise_on_duplicate:
            raise ValueError("Table already exists")
        # Return existing table instead of raising error

    return await ctx.tables.get_table_by_name(name)


@registry.register(
    default_title="List tables",
    description="Get a list of all available tables in the workspace.",
    display_group="Tables",
    namespace="core.table",
)
async def list_tables() -> list[dict[str, Any]]:
    ctx = get_context()
    return await ctx.tables.list_tables()


@registry.register(
    default_title="Get table metadata",
    description="Get a table's metadata by name. This includes the columns and whether they are indexed.",
    display_group="Tables",
    namespace="core.table",
)
async def get_table_metadata(
    name: Annotated[str, Doc("The name of the table to get.")],
) -> dict[str, Any]:
    ctx = get_context()
    return await ctx.tables.get_table_by_name(name)


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

    ctx = get_context()
    db_table = await ctx.tables.get_table_by_name(name)
    table_id = str(db_table["id"])

    rows = await ctx.tables.download(table_id, limit=limit)

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
