from __future__ import annotations

from collections.abc import Iterable
from io import TextIOBase
from typing import Any

from pydantic import BaseModel

from tracecat.db.models import Table, TableColumn
from tracecat.exceptions import TracecatImportError
from tracecat.tables.common import convert_value
from tracecat.tables.enums import SqlType
from tracecat.tables.schemas import TableColumnCreate
from tracecat.tables.service import sanitize_identifier
from tracecat.tables.service import TablesService


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


def _infer_value(value: str) -> tuple[Any, type | None]:
    text = value.strip()
    if not text:
        return None, None

    lowered = text.lower()
    if lowered in {"true", "false"}:
        return lowered == "true", bool

    try:
        return int(text), int
    except ValueError:
        pass

    try:
        return float(text), float
    except ValueError:
        pass

    return text, str


def _merge_types(current: type | None, new: type | None) -> type | None:
    if new is None:
        return current
    if current is None:
        return new
    if current is new:
        return current
    if {current, new} <= {int, float}:
        return float
    return str


def _python_type_to_sql(py_type: type | None) -> SqlType:
    if py_type is bool:
        return SqlType.BOOLEAN
    if py_type is int:
        return SqlType.INTEGER
    if py_type is float:
        return SqlType.NUMERIC
    return SqlType.TEXT


def _unique_column_name(
    existing: Iterable[str], raw_name: str, position: int
) -> str:
    candidate = "".join(c if c.isalnum() or c == "_" else "_" for c in raw_name)
    candidate = candidate.strip("_")
    if not candidate:
        candidate = f"column_{position}"
    if not candidate[0].isalpha():
        candidate = f"col_{candidate}"

    base = sanitize_identifier(candidate)
    name = base
    suffix = 1
    existing_set = set(existing)
    while name in existing_set:
        name = f"{base}_{suffix}"
        suffix += 1
    return name


def load_csv_table(csv_file: TextIOBase) -> tuple[list[TableColumnCreate], list[dict[str, Any]]]:
    import csv

    reader = csv.DictReader(csv_file)
    if reader.fieldnames is None:
        return [], []

    column_names: dict[str, str] = {}
    for idx, field in enumerate(reader.fieldnames):
        column_names[field] = _unique_column_name(column_names.values(), field, idx)

    column_types: dict[str, type | None] = {
        name: None for name in column_names.values()
    }
    rows: list[dict[str, Any]] = []

    for raw_row in reader:
        typed_row: dict[str, Any] = {}
        for original, sanitized in column_names.items():
            value = raw_row.get(original, "") or ""
            typed_value, value_type = _infer_value(value)
            typed_row[sanitized] = typed_value
            column_types[sanitized] = _merge_types(column_types[sanitized], value_type)
        rows.append(typed_row)

    columns = [
        TableColumnCreate(name=name, type=_python_type_to_sql(col_type))
        for name, col_type in column_types.items()
    ]

    return columns, rows
