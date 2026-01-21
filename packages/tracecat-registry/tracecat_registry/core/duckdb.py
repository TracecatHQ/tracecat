from typing import Annotated, Any

import duckdb
from pydantic_core import to_jsonable_python
from typing_extensions import Doc

from tracecat_registry import registry


def _rows_to_json(
    columns: list[str], rows: list[tuple[Any, ...]]
) -> list[dict[str, Any]]:
    records = [dict(zip(columns, row)) for row in rows]
    return to_jsonable_python(records, fallback=str, exclude_none=False)


@registry.register(
    default_title="Execute DuckDB SQL",
    description=("Execute SQL in an in-process DuckDB engine"),
    display_group="DuckDB",
    namespace="core.duckdb",
)
def execute_sql(
    sql: Annotated[
        str,
        Doc("SQL to execute in an in-process DuckDB connection. "),
    ],
) -> int | list[dict[str, Any]] | None:
    con = duckdb.connect()
    try:
        con.execute(sql)
        if con.description is None:
            return con.rowcount
        columns = [column[0] for column in con.description]
        if not columns:
            return con.rowcount
        rows = con.fetchall()
        return _rows_to_json(columns, rows)
    finally:
        con.close()
