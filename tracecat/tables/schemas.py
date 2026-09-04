import re
from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tracecat import config
from tracecat.core.schemas import Schema
from tracecat.identifiers import TableColumnID, TableID, TableRowID
from tracecat.query.aggregations import AggregationSpec
from tracecat.query.filters import Filter
from tracecat.tables.common import (
    coerce_multi_select_value,
    coerce_select_value,
    normalize_column_options,
)
from tracecat.tables.enums import SqlType

IDENTIFIER_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _validate_identifier_name(value: str, *, kind: str) -> str:
    if not IDENTIFIER_PATTERN.match(value):
        raise ValueError(
            f"{kind} name must contain only letters, numbers, and underscores, and start with a letter or underscore"
        )
    return value


class TableColumnRead(BaseModel):
    """Definition for a table column."""

    id: TableColumnID
    name: str
    type: SqlType
    nullable: bool = True
    default: Any | None = None
    is_index: bool = False
    options: list[str] | None = None


class TableColumnCreate(BaseModel):
    """Create model for a table column."""

    name: str = Field(
        ...,
        description="The name of the column",
        min_length=1,
        max_length=100,
    )
    type: SqlType = Field(
        ...,
        description="The SQL type of the column",
        min_length=1,
        max_length=100,
    )
    nullable: bool = True
    default: Any | None = None
    options: list[str] | None = None

    @field_validator("name")
    @classmethod
    def validate_column_name(cls, value: str) -> str:
        """Validate column name to prevent SQL injection."""
        return _validate_identifier_name(value, kind="Column")

    @model_validator(mode="after")
    def validate_enum_options(self) -> "TableColumnCreate":
        normalized = normalize_column_options(self.options)
        self.options = normalized

        if self.type in (SqlType.SELECT, SqlType.MULTI_SELECT):
            if not self.options:
                raise ValueError(
                    "SELECT and MULTI_SELECT columns must define at least one option"
                )
            if self.default is not None:
                if self.type is SqlType.SELECT:
                    self.default = coerce_select_value(
                        self.default, options=self.options
                    )
                elif self.type is SqlType.MULTI_SELECT:
                    self.default = coerce_multi_select_value(
                        self.default, options=self.options
                    )
        elif self.options:
            raise ValueError("Options are only supported for SELECT or MULTI_SELECT")

        return self


class TableColumnUpdate(BaseModel):
    """Update model for a table column."""

    name: str | None = Field(
        default=None,
        description="The name of the column",
        min_length=1,
        max_length=100,
    )
    type: SqlType | None = Field(
        default=None,
        description="The SQL type of the column",
        min_length=1,
        max_length=100,
    )
    nullable: bool | None = Field(
        default=None,
        description="Whether the column can be null",
    )
    default: Any | None = Field(
        default=None,
        description="The default value of the column",
    )
    is_index: bool | None = Field(
        default=None,
        description="True creates a unique index, False drops it, None leaves unchanged.",
    )
    options: list[str] | None = Field(default=None)

    @field_validator("name")
    @classmethod
    def validate_column_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_identifier_name(value, kind="Column")

    @model_validator(mode="after")
    def normalise_options(self) -> "TableColumnUpdate":
        if self.options is not None:
            self.options = normalize_column_options(self.options) or []
        return self


class TableRowRead(BaseModel):
    """Read model for a table row."""

    model_config = ConfigDict(extra="allow")
    id: TableRowID
    created_at: datetime
    updated_at: datetime


class TableRowUpdate(BaseModel):
    """Update model for a table row."""

    data: dict[str, Any]


class TableRowInsert(BaseModel):
    """Insert model for a table row."""

    data: dict[str, Any]
    upsert: bool = False


class TableRowInsertBatch(BaseModel):
    """Request body for batch inserting rows."""

    rows: list[dict[str, Any]]
    upsert: bool = False


class TableRowInsertBatchResponse(BaseModel):
    """Response for batch insert operation."""

    rows_inserted: int


class TableRowBatchDelete(BaseModel):
    """Request body for batch deleting rows."""

    row_ids: list[UUID] = Field(..., min_length=1, max_length=1000)


class TableRowBatchDeleteResponse(BaseModel):
    """Response for batch delete operation."""

    rows_deleted: int


class TableRowBatchUpdate(BaseModel):
    """Request body for batch updating rows."""

    row_ids: list[UUID] = Field(..., min_length=1, max_length=1000)
    data: dict[str, Any] = Field(..., min_length=1)


class TableRowBatchUpdateResponse(BaseModel):
    """Response for batch update operation."""

    rows_updated: int


type TableAggregateValue = str | bool | int | float | datetime | date | None


class TableAggregateRequest(AggregationSpec):
    """Filter and aggregate rows in one workspace table.

    ``sum`` over INTEGER or NUMERIC, every ``mean`` and ``median``, and
    ``min``/``max`` over NUMERIC are widened to float8 JSON numbers. TEXT and
    SELECT group keys use their first 256 characters, so longer values with the
    same prefix collapse into one group.
    """

    filters: Filter | None = Field(default=None)
    limit: int = Field(
        default=config.TRACECAT__LIMIT_AGG_GROUPS_DEFAULT,
        ge=1,
        le=config.TRACECAT__LIMIT_AGG_GROUPS_MAX,
    )


class AggregateResponse(BaseModel):
    """Flat aggregation groups and whether the configured limit was exceeded."""

    # Output keys are caller-defined aliases, so a fixed object model cannot
    # describe each group. Values remain a closed union of supported SQL types.
    groups: list[dict[str, TableAggregateValue]]
    truncated: bool


class TableReadMinimal(Schema):
    """Read model for a table."""

    id: TableID
    name: str
    created_at: datetime
    updated_at: datetime


class TableRead(Schema):
    """Read model for a table."""

    id: TableID
    name: str
    columns: list[TableColumnRead]


class InferredColumn(BaseModel):
    """Inferred column mapping between CSV headers and table columns."""

    csv_header: str = Field(
        ...,
        description="Original column header in the CSV file",
    )
    field_name: str = Field(
        ...,
        description="Column name created in Tracecat",
    )
    field_type: SqlType = Field(
        ...,
        description="Inferred SQL type for the column",
    )


class TableCreate(BaseModel):
    """Create model for a table."""

    name: str = Field(
        ...,
        description="The name of the table",
        min_length=1,
        max_length=100,
    )
    columns: list[TableColumnCreate] = Field(
        default_factory=list,
        description="The columns of the table",
    )

    @field_validator("name")
    @classmethod
    def validate_table_name(cls, value: str) -> str:
        """Validate table name to prevent SQL injection."""
        return _validate_identifier_name(value, kind="Table")


class TableUpdate(BaseModel):
    """Update model for a table."""

    name: str | None = Field(
        default=None,
        description="The name of the table",
        min_length=1,
        max_length=100,
    )

    @field_validator("name")
    @classmethod
    def validate_table_name(cls, value: str | None) -> str | None:
        """Validate table name to prevent SQL injection."""
        if value is None:
            return None
        return _validate_identifier_name(value, kind="Table")


class TableImportResponse(BaseModel):
    """Response model for importing a table from CSV."""

    table: TableRead
    rows_inserted: int
    column_mapping: list[InferredColumn]
