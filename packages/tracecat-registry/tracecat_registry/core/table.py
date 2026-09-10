from datetime import datetime
from typing import Annotated, Any, Literal

from typing_extensions import Doc

from tracecat_registry import config, ctx, registry, types
from tracecat_registry.sdk.exceptions import TracecatConflictError


# Query inputs deliberately remain plain data: action schema consumers cannot
# resolve recursive filter models, and the registry cannot import server models.
@registry.register(
    default_title="Aggregate rows",
    description=(
        "Filter, group, and summarize table rows. Returns groups and a truncated "
        "flag indicating whether more groups exist than the requested limit."
    ),
    display_group="Tables",
    namespace="core.table",
)
async def aggregate_rows(
    table: Annotated[str, Doc("The name of the workspace table to aggregate.")],
    group_by: Annotated[
        list[str | dict[str, Any]],
        Doc(
            "Choose how to split rows into groups. Use up to 3 fields, or `[]` for one "
            "total across all matching rows.\n"
            "\n"
            "Supply a field name such as `source`, or an object with `field` and "
            "optional `bucket`, `timezone`, and `alias`. For example: ['source', "
            "{'field': 'created_at', 'bucket': 'hour'}]. An alias names the field in "
            "the result; it defaults to the field name. All output names must be "
            "unique and at most 63 UTF-8 bytes.\n"
            "\n"
            "Fields you can group by:\n"
            "\n"
            "- TEXT, SELECT, INTEGER, NUMERIC, and BOOLEAN columns.\n"
            "- DATE and TIMESTAMPTZ columns, including the system fields `created_at` "
            "and `updated_at`. These require a `bucket`: `hour`, `day`, `week`, or "
            "`month`. Weeks start on Monday.\n"
            "\n"
            "JSONB, MULTI_SELECT, `id`, and internal columns are unsupported.\n"
            "\n"
            "Date and time settings:\n"
            "\n"
            "- Timestamps accept an IANA timezone name, such as `America/New_York`. "
            "The default is `UTC`; results always contain UTC timestamps.\n"
            "- DATE fields return `YYYY-MM-DD`. They do not accept a timezone, and "
            "even an `hour` bucket retains only date precision.\n"
            "\n"
            "How group values appear in results:\n"
            "\n"
            "- Missing values share one `null` group.\n"
            "- TEXT and SELECT values use only the first 256 characters. Values with "
            "the same prefix merge into one group.\n"
            "- NUMERIC values appear as exact decimal strings."
        ),
    ],
    filters: Annotated[
        dict[str, Any] | None,
        Doc(
            "Choose which rows to include before grouping. Omit this input to include "
            "all rows.\n"
            "\n"
            "Write one condition as {'field': 'amount', 'op': 'gte', 'value': 10}. "
            "Combine conditions with {'and': [...]}, {'or': [...]}, or {'not': {...}}.\n"
            "\n"
            "Choose an operator supported by the column type:\n"
            "\n"
            "- TEXT: `eq`, `ne`, `in`, `not_in`, `is_null`, `contains`, and "
            "`starts_with`. The text operators ignore case and match literal text.\n"
            "- INTEGER and NUMERIC: `eq`, `ne`, `in`, `not_in`, `gt`, `gte`, `lt`, "
            "`lte`, and `is_null`.\n"
            "- DATE and TIMESTAMPTZ: `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, and "
            "`is_null`.\n"
            "- SELECT: `eq`, `ne`, `in`, `not_in`, and `is_null`.\n"
            "- BOOLEAN: `eq`, `ne`, and `is_null`.\n"
            "\n"
            "Supply a list for `in` or `not_in`. Omit `value` for `is_null`. Use "
            "strings for exact decimals and ISO-formatted dates or timestamps.\n"
            "\n"
            "`ne` and `not_in` exclude missing values. An empty `not_in` list matches "
            "all rows; an empty `in` list matches none.\n"
            "\n"
            "Filters allow up to 4 levels of nesting, 50 conditions, and 1000 total "
            "values. The server validates the request when the action runs."
        ),
    ] = None,
    aggs: Annotated[
        list[dict[str, Any]] | None,
        Doc(
            "Choose what to calculate for each group. Omit this input to count rows. "
            "Supply up to 8 calculations; an empty list is invalid.\n"
            "\n"
            "Each calculation is an object with `function`, optional `field`, and "
            "optional `alias`. For example: [{'function': 'sum', 'field': 'bytes_out', "
            "'alias': 'total_bytes'}].\n"
            "\n"
            "Available calculations:\n"
            "\n"
            "- `count`: Count rows when you omit `field`, or count non-null values "
            "when you supply it.\n"
            "- `count_distinct`: Count different non-null values.\n"
            "- `sum`, `mean`, `median`: Calculate the total, average, or middle value.\n"
            "- `min`, `max`: Return the smallest or largest value.\n"
            "\n"
            "Every function except `count` requires a field. Numeric columns support "
            "all functions. Text and date/time columns support `count`, "
            "`count_distinct`, `min`, and `max`. BOOLEAN and SELECT columns support "
            "only `count` and `count_distinct`.\n"
            "\n"
            "Naming and number formats:\n"
            "\n"
            "- Use `alias` to name a result, such as `total_bytes`. Otherwise the name "
            "is `count` or `function_field`. All output names must be unique and at "
            "most 63 UTF-8 bytes.\n"
            "- Counts are integers. INTEGER/NUMERIC sums, all means and medians, and "
            "NUMERIC min/max use floating-point numbers and can lose precision. "
            "NUMERIC grouping values remain exact decimal strings."
        ),
    ] = None,
    limit: Annotated[
        int | None,
        Doc(
            "Set the maximum number of groups to return. Use at least 1, up to your "
            "server's configured maximum (normally 1000). Omit this input to use the "
            "server default (normally 100).\n"
            "\n"
            "If more groups exist, the result sets `truncated` to `true`. There is no "
            "next-page cursor."
        ),
    ] = None,
    min_count: Annotated[
        int | None, Doc("Only return groups with at least this many rows (minimum 1).")
    ] = None,
    order_by: Annotated[
        str | None,
        Doc(
            "Choose a group or calculation output name to sort by, including any alias "
            "you set.\n"
            "\n"
            "If omitted, results sort by the first date/time bucket, or by the first "
            "calculation when there is no date/time bucket."
        ),
    ] = None,
    sort: Annotated[
        Literal["asc", "desc"] | None,
        Doc(
            "Use `asc` for ascending order or `desc` for descending order.\n"
            "\n"
            "If omitted, the direction is `asc` when the action automatically sorts by "
            "a date/time bucket. Otherwise it is `desc`, including when you set "
            "`order_by` yourself. Missing values sort last; group values break ties."
        ),
    ] = None,
) -> types.AggregateResponse:
    # The recursive query remains plain JSON; only an omitted limit is removed
    # so the server can apply its configured default and maximum.
    spec: dict[str, Any] = {
        "filters": filters,
        "group_by": group_by,
        "aggs": aggs,
        "min_count": min_count,
        "order_by": order_by,
        "sort": sort,
    }
    if limit is not None:
        spec["limit"] = limit
    return await ctx.tables.aio.aggregate_rows(table_name=table, spec=spec)


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
    return await ctx.tables.aio.lookup(table=table, column=column, value=value)


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
    return await ctx.tables.aio.exists(table=table, column=column, value=value)


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
    if limit > config.TRACECAT__LIMIT_CURSOR_MAX:
        raise ValueError(
            f"Limit cannot be greater than {config.TRACECAT__LIMIT_CURSOR_MAX}"
        )

    params: dict[str, Any] = {
        "table": table,
        "column": column,
        "value": value,
    }
    if limit is not None:
        params["limit"] = limit
    return await ctx.tables.aio.lookup_many(**params)


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
    paginate: Annotated[
        bool,
        Doc("If true, return cursor pagination metadata along with items."),
    ] = False,
) -> types.TableSearchResponse | list[dict[str, Any]]:
    if limit > config.TRACECAT__LIMIT_CURSOR_MAX:
        raise ValueError(
            f"Limit cannot be greater than {config.TRACECAT__LIMIT_CURSOR_MAX}"
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
    response = await ctx.tables.aio.search_rows(**params)
    if paginate:
        return response
    if isinstance(response, dict):
        return response.get("items")
    return response


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
    return await ctx.tables.aio.insert_row(
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
    return await ctx.tables.aio.insert_rows(
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
    return await ctx.tables.aio.update_row(
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
    await ctx.tables.aio.delete_row(table=table, row_id=row_id)


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
            "List of column definitions. Each item is an object with required "
            "`name` and uppercase `type`, plus optional `nullable`, `default`, "
            "and `options` fields. Use `TEXT`, `INTEGER`, `NUMERIC`, "
            "`BOOLEAN`, `DATE`, `TIMESTAMPTZ`, `JSONB`, `SELECT`, or "
            "`MULTI_SELECT`. `options` is required for `SELECT` and "
            "`MULTI_SELECT`, and invalid for other types."
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
        return await ctx.tables.aio.create_table(**client_params)
    except TracecatConflictError as exc:
        raise ValueError("Table already exists") from exc


@registry.register(
    default_title="List tables",
    description="Get a list of all available tables in the workspace.",
    display_group="Tables",
    namespace="core.table",
)
async def list_tables() -> list[types.Table]:
    return await ctx.tables.aio.list_tables()


@registry.register(
    default_title="Get table metadata",
    description="Get a table's metadata by name. This includes the columns and whether they are indexed.",
    display_group="Tables",
    namespace="core.table",
)
async def get_table_metadata(
    name: Annotated[str, Doc("The name of the table to get.")],
) -> types.TableRead:
    return await ctx.tables.aio.get_table_metadata(name)


@registry.register(
    default_title="Update table",
    description="Rename a table by name.",
    display_group="Tables",
    namespace="core.table",
)
async def update_table(
    name: Annotated[
        str,
        Doc("The current name of the table to update."),
    ],
    new_name: Annotated[
        str,
        Doc("The new table name."),
    ],
) -> types.TableRead:
    return await ctx.tables.aio.update_table(name=name, new_name=new_name)


@registry.register(
    default_title="Create column",
    description="Add a column to an existing table.",
    display_group="Tables",
    namespace="core.table",
)
async def create_column(
    table: Annotated[
        str,
        Doc("The table to add the column to."),
    ],
    column: Annotated[
        dict[str, Any],
        Doc(
            "Column definition with required `name` and uppercase `type`, plus "
            "optional `nullable`, `default`, and `options` fields. Use `TEXT`, "
            "`INTEGER`, `NUMERIC`, `BOOLEAN`, `DATE`, `TIMESTAMPTZ`, `JSONB`, "
            "`SELECT`, or `MULTI_SELECT`. `options` is required for `SELECT` "
            "and `MULTI_SELECT`."
        ),
    ],
) -> types.TableRead:
    return await ctx.tables.aio.create_column(table=table, column=column)


@registry.register(
    default_title="Update column",
    description="Update a table column's name, type, nullability, default, index, or options.",
    display_group="Tables",
    namespace="core.table",
)
async def update_column(
    table: Annotated[
        str,
        Doc("The table containing the column."),
    ],
    column: Annotated[
        str,
        Doc("The current column name."),
    ],
    update: Annotated[
        dict[str, Any],
        Doc(
            "Partial column update. Supported fields: `name`, `type`, "
            "`nullable`, `default`, `is_index`, and `options`."
        ),
    ],
) -> types.TableRead:
    return await ctx.tables.aio.update_column(
        table=table,
        column=column,
        update=update,
    )


@registry.register(
    default_title="Delete column",
    description="Delete a column from an existing table.",
    display_group="Tables",
    namespace="core.table",
)
async def delete_column(
    table: Annotated[
        str,
        Doc("The table containing the column."),
    ],
    column: Annotated[
        str,
        Doc("The column name to delete."),
    ],
) -> types.TableRead:
    return await ctx.tables.aio.delete_column(table=table, column=column)


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
    limit: Annotated[
        int, Doc("The maximum number of rows to download.")
    ] = config.TRACECAT__LIMIT_TABLE_DOWNLOAD_MAX,
) -> list[dict[str, Any]] | str:
    if limit > config.TRACECAT__LIMIT_TABLE_DOWNLOAD_MAX:
        raise ValueError(
            f"Cannot return more than {config.TRACECAT__LIMIT_TABLE_DOWNLOAD_MAX} rows"
        )

    params: dict[str, Any] = {"table": name}
    if format is not None:
        params["format"] = format
    if limit is not None:
        params["limit"] = limit
    return await ctx.tables.aio.download(**params)
