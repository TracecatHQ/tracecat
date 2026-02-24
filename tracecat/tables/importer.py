from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Self
from uuid import UUID

import orjson
from pydantic import BaseModel

from tracecat.db.models import Table, TableColumn
from tracecat.exceptions import TracecatImportError
from tracecat.tables.common import coerce_to_utc_datetime, convert_value
from tracecat.tables.enums import SqlType

if TYPE_CHECKING:
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

    def convert_value(self, value: str | None, col_type: SqlType) -> Any:
        """Convert string value to appropriate Python type based on column type."""
        if value is None:
            return None
        if not isinstance(value, str):
            return convert_value(str(value), col_type)

        if value.strip() == "":
            if col_type is SqlType.TEXT:
                return ""
            return None

        normalized = value if col_type is SqlType.TEXT else value.strip()
        return convert_value(normalized, col_type)

    def map_row(
        self,
        csv_row: Mapping[str, str | None],
        column_mapping: Mapping[str, str],
        *,
        row_number: int | None = None,
    ) -> dict[str, Any]:
        """Map a single CSV row to table columns with type conversion."""
        mapped_row: dict[str, Any] = {}
        normalized_row = {
            normalize_csv_header(key): value for key, value in csv_row.items()
        }

        for csv_col, table_col in column_mapping.items():
            if not table_col or table_col == "skip":
                continue

            if col_info := self.columns.get(table_col):
                if csv_col in csv_row:
                    value = csv_row[csv_col]
                else:
                    normalized_col = normalize_csv_header(csv_col)
                    if normalized_col in normalized_row:
                        value = normalized_row[normalized_col]
                    else:
                        row_suffix = (
                            f" at CSV row {row_number}"
                            if row_number is not None
                            else ""
                        )
                        raise TracecatImportError(
                            f"Mapped CSV column {csv_col!r} was not found"
                            f" in file headers{row_suffix}"
                        )
                if isinstance(value, str) and "\x00" in value:
                    row_suffix = (
                        f" at CSV row {row_number}" if row_number is not None else ""
                    )
                    raise TracecatImportError(
                        f"Invalid null byte in column {table_col!r}{row_suffix}"
                    )
                try:
                    mapped_row[table_col] = self.convert_value(value, col_info.type)
                except TypeError as e:
                    row_suffix = (
                        f" at CSV row {row_number}" if row_number is not None else ""
                    )
                    raise TracecatImportError(
                        f"Cannot convert value {value!r} in column {table_col!r}"
                        f" to SQL type {col_info.type}{row_suffix}"
                    ) from e

        return mapped_row

    async def process_chunk(
        self, chunk: list[dict[str, Any]], service: "TablesService", table: Table
    ) -> None:
        """Process and insert a chunk of rows."""
        if chunk:
            count = await service.batch_insert_rows(
                table, chunk, chunk_size=self.chunk_size
            )
            self.total_rows_inserted += count


def _normalise_identifier(raw: str | None, *, prefix: str) -> str:
    """Sanitise an identifier while preserving table naming rules."""
    candidate = "".join(
        c for c in (raw or "").strip() if c.isalnum() or c == "_"
    ).lower()
    if not candidate:
        candidate = prefix
    if not candidate[0].isalpha():
        candidate = f"{prefix}_{candidate}"
    return candidate


def _ensure_unique_identifier(base: str, used: set[str]) -> str:
    """Ensure identifiers remain unique by appending numeric suffixes."""
    if base not in used:
        used.add(base)
        return base
    suffix = 1
    while True:
        candidate = f"{base}_{suffix}"
        if candidate not in used:
            used.add(candidate)
            return candidate
        suffix += 1


def generate_table_name(raw_name: str | None) -> str:
    """Generate a valid base table name from raw input."""
    return _normalise_identifier(raw_name, prefix="table")


def _looks_like_json(value: str) -> bool:
    stripped = value.lstrip()
    return stripped.startswith("{") or stripped.startswith("[")


def normalize_csv_header(header: str) -> str:
    """Normalize CSV headers for robust matching across parsers."""
    return header.lstrip("\ufeff")


@dataclass(slots=True)
class ColumnStats:
    original_name: str
    name: str
    seen_non_empty: bool = False
    bool_candidate: bool = True
    int_candidate: bool = True
    numeric_candidate: bool = True
    timestamp_candidate: bool = True
    uuid_candidate: bool = True
    json_candidate: bool = True

    def observe(self, value: str | None) -> None:
        if value is None:
            return
        stripped = value.strip()
        if stripped == "":
            return

        self.seen_non_empty = True

        if self.bool_candidate:
            lowered = stripped.lower()
            if lowered not in {"true", "false", "1", "0"}:
                self.bool_candidate = False

        if self.int_candidate:
            try:
                int(stripped)
            except ValueError:
                self.int_candidate = False

        if self.numeric_candidate:
            try:
                float(stripped)
            except ValueError:
                self.numeric_candidate = False

        if self.timestamp_candidate:
            try:
                coerce_to_utc_datetime(stripped)
            except (TypeError, ValueError):
                self.timestamp_candidate = False

        if self.uuid_candidate:
            try:
                UUID(stripped)
            except ValueError:
                self.uuid_candidate = False

        if self.json_candidate:
            if not _looks_like_json(stripped):
                self.json_candidate = False
            else:
                try:
                    orjson.loads(stripped)
                except orjson.JSONDecodeError:
                    self.json_candidate = False

    def infer_type(self) -> SqlType:
        if not self.seen_non_empty:
            return SqlType.TEXT
        if self.bool_candidate:
            return SqlType.BOOLEAN
        if self.int_candidate:
            return SqlType.INTEGER
        if self.numeric_candidate:
            return SqlType.NUMERIC
        if self.timestamp_candidate:
            return SqlType.TIMESTAMPTZ
        if self.uuid_candidate:
            return SqlType.UUID
        if self.json_candidate:
            return SqlType.JSONB
        return SqlType.TEXT


class InferredCSVColumn(ColumnInfo):
    original_name: str


class CSVSchemaInferer:
    """Infer column metadata from CSV headers and rows."""

    def __init__(self, headers: Sequence[str | None]) -> None:
        if not headers:
            raise TracecatImportError("CSV file must include a header row")

        normalised_headers = [(header or "").strip() for header in headers]
        duplicates: list[str] = []
        for name, count in Counter(normalised_headers).items():
            if count > 1:
                display = name if name else "<empty>"
                duplicates.append(display)
        if duplicates:
            duplicates_str = ", ".join(sorted(duplicates))
            raise TracecatImportError(
                f"CSV headers must be unique. Duplicate columns: {duplicates_str}"
            )

        used: set[str] = set()
        self._columns: list[ColumnStats] = []
        self.column_mapping: dict[str, str] = {}

        for index, header in enumerate(headers, start=1):
            header_value = header or ""
            base = _normalise_identifier(header_value, prefix=f"col_{index}")
            name = _ensure_unique_identifier(base, used)
            stats = ColumnStats(original_name=header_value, name=name)
            self._columns.append(stats)
            self.column_mapping[header_value] = name

    @classmethod
    def initialise(cls, headers: Sequence[str | None]) -> Self:
        return cls(headers)

    def observe(self, row: dict[str, str]) -> None:
        for stats in self._columns:
            stats.observe(row.get(stats.original_name))

    def result(self) -> list[InferredCSVColumn]:
        return [
            InferredCSVColumn(
                original_name=stats.original_name,
                name=stats.name,
                type=stats.infer_type(),
            )
            for stats in self._columns
        ]
