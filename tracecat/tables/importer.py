import json
from typing import Any

import polars as pl
from pydantic import BaseModel

from tracecat.db.schemas import Table, TableColumn
from tracecat.tables.enums import SqlType
from tracecat.tables.service import TablesService
from tracecat.types.exceptions import TracecatImportError


class ColumnInfo(BaseModel):
    name: str
    type: SqlType


class CSVImporter:
    def __init__(
        self, table_columns: list[TableColumn], chunk_size: int = 1000
    ) -> None:
        self.columns: dict[str, ColumnInfo] = {
            col.name: ColumnInfo(name=col.name, type=SqlType(col.type))
            for col in table_columns
        }
        self.chunk_size = chunk_size
        self.total_rows_inserted = 0

        # Map SQL types to Polars dtypes for efficient conversion
        self.type_mapping = {
            SqlType.TEXT: pl.Utf8,
            SqlType.INTEGER: pl.Int64,
            SqlType.DECIMAL: pl.Float64,
            SqlType.BOOLEAN: pl.Boolean,
            SqlType.JSONB: pl.Utf8,  # Store JSONB as strings
        }

    def map_row(
        self, row: dict[str, Any], column_mapping: dict[str, str]
    ) -> dict[str, Any]:
        """Map a CSV row to table schema using column mapping"""
        mapped_row = {}

        for csv_col, table_col in column_mapping.items():
            if not table_col or table_col == "skip" or csv_col not in row:
                continue

            value = row[csv_col]
            col_info = self.columns.get(table_col)

            if col_info:
                # Simple type conversion based on SQL type
                try:
                    if col_info.type == SqlType.INTEGER and value != "":
                        value = int(value)
                    elif col_info.type == SqlType.DECIMAL and value != "":
                        value = float(value)
                    elif col_info.type == SqlType.BOOLEAN and value != "":
                        if isinstance(value, str):
                            value = value.lower() in ("true", "yes", "1", "t", "y")
                    elif col_info.type == SqlType.JSONB:
                        # Handle JSON data - convert to string if needed
                        if value:
                            if not isinstance(value, str):
                                # Object to JSON string
                                try:
                                    value = json.dumps(value)
                                except (TypeError, ValueError) as e:
                                    raise TracecatImportError(
                                        f"Cannot convert value to JSON: {e}"
                                    ) from e
                            else:
                                # Validate it's a proper JSON string
                                try:
                                    # Parse and re-stringify to ensure valid JSON format
                                    json.loads(value)
                                except json.JSONDecodeError as e:
                                    raise TracecatImportError(
                                        f"Invalid JSON format: {e}"
                                    ) from e
                except (ValueError, TypeError) as e:
                    raise TracecatImportError(f"Failed to convert value: {e}") from e

            mapped_row[table_col] = value

        return mapped_row

    async def process_chunk(
        self, chunk: list[dict[str, Any]], service: TablesService, table: Table
    ) -> None:
        """Process chunk of rows using Polars for efficiency"""
        if not chunk:
            return

        try:
            # Convert chunk to Polars DataFrame
            df = pl.DataFrame(chunk)

            # Apply type conversions based on schema
            for col_name in df.columns:
                col_info = self.columns.get(col_name)
                if col_info and col_name in df.columns:
                    pl_type = self.type_mapping.get(col_info.type)

                    # Special handling for JSONB columns
                    if col_info.type == SqlType.JSONB:
                        try:
                            # Ensure JSON values are proper strings
                            df = df.with_column(
                                pl.col(col_name).map_elements(
                                    lambda x: json.dumps(x)
                                    if not isinstance(x, str)
                                    else x,
                                    return_dtype=pl.Utf8,
                                )
                            )
                        except Exception as e:
                            raise TracecatImportError(
                                f"Failed to convert column '{col_name}': {e}"
                            ) from e
                    elif pl_type:
                        try:
                            df = df.with_column(
                                pl.col(col_name).cast(pl_type, strict=False)
                            )
                        except Exception as e:
                            raise TracecatImportError(
                                f"Failed to cast column '{col_name}' to {pl_type}: {e}"
                            ) from e

            # Convert back to dicts for database insertion
            rows = df.to_dicts()

            # Insert the processed data
            count = await service.batch_insert_rows(table, rows)
            self.total_rows_inserted += count
        except Exception as e:
            raise TracecatImportError(f"Error processing chunk: {str(e)}") from e
