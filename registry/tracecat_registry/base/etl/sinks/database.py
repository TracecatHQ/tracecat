"""Sink data (e.g. alerts) to a database."""

from typing import Annotated, Literal

import polars as pl
from pydantic import Field

from tracecat_registry import registry


@registry.register(
    default_title="Write to database",
    description="Write list of JSON objects to a database using the ADBC engine.",
    display_group="Sinks",
    namespace="integrations.sinks",
)
def write_to_database(
    data: Annotated[
        list[dict[str, str | int | float | bool]],
        Field(..., description="The list of JSON objects to write to the database"),
    ],
    table_name: Annotated[
        str, Field(..., description="The name of the table to write to")
    ],
    uri: Annotated[str, Field(..., description="Database URI string")],
    if_table_exists: Annotated[
        Literal["append", "replace", "fail"],
        Field(..., description="The behavior if the table already exists"),
    ],
) -> int:
    """Write list of JSON objects to a database using the ADBC engine.

    Supported databases:
    - SQLite
    - PostgreSQL
    - Snowflake

    Returns the number of rows written to the database if supported, otherwise returns -1.
    """

    df = pl.from_dicts(data)
    n_rows = df.write_database(
        table_name=table_name,
        connection=uri,
        if_table_exists=if_table_exists,
        engine="adbc",
    )
    return n_rows
