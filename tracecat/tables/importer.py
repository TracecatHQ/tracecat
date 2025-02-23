from typing import Any

from pydantic import BaseModel

from tracecat.db.schemas import Table, TableColumn
from tracecat.tables.common import convert_value
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

    def convert_value(self, value: str, col_type: SqlType) -> Any:
        """Convert string value to appropriate Python type based on column type."""
        if not value:  # Skip empty values
            return value
        return convert_value(value, col_type)

    def map_row(
        self, csv_row: dict[str, str], column_mapping: dict[str, str]
    ) -> dict[str, Any]:
        """Map a single CSV row to table columns with type conversion."""
        mapped_row: dict[str, Any] = {}

        for csv_col, table_col in column_mapping.items():
            if not table_col or table_col == "skip":
                continue

            if col_info := self.columns.get(table_col):
                value = csv_row[csv_col]
                try:
                    mapped_row[table_col] = self.convert_value(value, col_info.type)
                except TypeError as e:
                    raise TracecatImportError(
                        f"Cannot convert value {value!r} in column {table_col!r}"
                        f" to SQL type {col_info.type}"
                    ) from e

        return mapped_row

    async def process_chunk(
        self, chunk: list[dict[str, Any]], service: TablesService, table: Table
    ) -> None:
        """Process and insert a chunk of rows."""
        if chunk:
            count = await service.batch_insert_rows(table, chunk)
            self.total_rows_inserted += count
