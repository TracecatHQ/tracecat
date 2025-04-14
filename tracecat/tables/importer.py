import json
import logging
from typing import Any

import polars as pl
from pydantic import BaseModel

from tracecat.db.schemas import Table, TableColumn
from tracecat.tables.enums import SqlType
from tracecat.tables.service import TablesService
from tracecat.types.exceptions import TracecatImportError

logger = logging.getLogger(__name__)


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

    def convert_value(self, value: Any, sql_type: SqlType) -> Any:
        """Convert a value to the appropriate type based on SQL type."""
        try:
            if sql_type == SqlType.INTEGER and value != "":
                return int(value)
            elif sql_type == SqlType.DECIMAL and value != "":
                return float(value)
            elif sql_type == SqlType.BOOLEAN and value != "":
                if isinstance(value, str):
                    lowercase_value = value.lower()
                    if lowercase_value in ("true", "yes", "1", "t", "y"):
                        return True
                    elif lowercase_value in ("false", "no", "0", "f", "n"):
                        return False
                    else:
                        raise ValueError(f"Invalid boolean value: {value}")
                return bool(value)
            elif sql_type == SqlType.JSONB:
                if value:
                    if not isinstance(value, str):
                        # Object to JSON string
                        try:
                            return json.dumps(value)
                        except (TypeError, ValueError) as e:
                            raise TracecatImportError(
                                f"Cannot convert value to JSON: {e}"
                            ) from e
                    else:
                        # Validate it's a proper JSON string
                        try:
                            # Parse and re-stringify to ensure valid JSON format
                            json.loads(value)
                            return value
                        except json.JSONDecodeError as e:
                            raise TracecatImportError(
                                f"Invalid JSON format: {e}"
                            ) from e
            return value
        except (ValueError, TypeError) as e:
            raise TracecatImportError(
                f"Cannot convert value '{value}' to SqlType {sql_type}: {e}"
            ) from e

    def map_row(
        self, row: dict[str, Any], column_mapping: dict[str, str]
    ) -> dict[str, Any]:
        """Map a CSV row to table schema using column mapping"""
        mapped_row = {}

        for csv_col, table_col in column_mapping.items():
            if not table_col or table_col == "skip" or csv_col not in row:
                continue

            # Skip columns that don't exist in the table schema
            if col_info := self.columns.get(table_col):
                value = row[csv_col]
                try:
                    mapped_row[table_col] = self.convert_value(value, col_info.type)
                except Exception as e:
                    logger.warning(f"Failed to convert {csv_col} to {table_col}: {e}")
                    raise TracecatImportError(
                        f"Cannot convert value '{value}' in column '{table_col}' to SQL type {col_info.type}"
                    ) from e

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
                            df = df.with_columns(
                                pl.col(col_name).map_elements(
                                    lambda x: json.dumps(x)
                                    if not isinstance(x, str)
                                    else x,
                                    return_dtype=pl.Utf8,
                                )
                            )
                        except Exception as e:
                            logger.warning(
                                f"Failed to convert column '{col_name}': {e}"
                            )
                            raise TracecatImportError(
                                f"Failed to convert column '{col_name}': {e}"
                            ) from e
                    elif pl_type:
                        try:
                            df = df.with_columns(
                                pl.col(col_name).cast(pl_type, strict=False)
                            )
                        except Exception as e:
                            logger.warning(
                                f"Failed to cast column '{col_name}' to {pl_type}: {e}"
                            )
                            raise TracecatImportError(
                                f"Failed to cast column '{col_name}' to {pl_type}: {e}"
                            ) from e

            # Convert back to dicts for database insertion
            rows = df.to_dicts()

            # Insert the processed data
            count = await service.batch_insert_rows(table, rows)
            self.total_rows_inserted += count
        except Exception as e:
            logger.error(f"Error processing chunk: {e}")
            raise TracecatImportError(f"Error processing chunk: {str(e)}") from e
