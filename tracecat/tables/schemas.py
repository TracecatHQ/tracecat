import re
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tracecat.core.schemas import Schema
from tracecat.identifiers import TableColumnID, TableID, TableRowID
from tracecat.pagination import CursorPaginatedResponse
from tracecat.tables.common import (
    coerce_multi_select_value,
    coerce_select_value,
    normalize_column_options,
)
from tracecat.tables.enums import SqlType


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
        # Only allow alphanumeric characters and underscores
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", value):
            raise ValueError(
                "Column name must contain only letters, numbers, and underscores, and start with a letter or underscore"
            )
        return value

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
        description="Whether the column is an index",
    )
    options: list[str] | None = Field(default=None)

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


class TableReadMinimal(Schema):
    """Read model for a table."""

    id: TableID
    name: str


class TableRead(Schema):
    """Read model for a table."""

    id: TableID
    name: str
    columns: list[TableColumnRead]


class TableAggregation(StrEnum):
    SUM = "sum"
    MIN = "min"
    MAX = "max"
    MEAN = "mean"
    MEDIAN = "median"
    MODE = "mode"
    N_UNIQUE = "n_unique"
    VALUE_COUNTS = "value_counts"


type TableAggregationScalar = int | float | str | bool | datetime | UUID | None


class TableAggregationBucket(Schema):
    group: TableAggregationScalar
    value: TableAggregationScalar


class TableAggregationRead(Schema):
    agg: TableAggregation
    group_by: str | None = None
    agg_field: str | None = None
    value: TableAggregationScalar = None
    buckets: list[TableAggregationBucket] = Field(default_factory=list)


class TableLookupResponse(Schema):
    items: list[dict[str, Any]]
    aggregation: TableAggregationRead | None = None


class TableSearchResponse(CursorPaginatedResponse[dict[str, Any]]):
    aggregation: TableAggregationRead | None = None


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
        # Only allow alphanumeric characters and underscores
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", value):
            raise ValueError(
                "Table name must contain only letters, numbers, and underscores, and start with a letter or underscore"
            )
        return value


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
    def validate_table_name(cls, value: str) -> str:
        """Validate table name to prevent SQL injection."""
        if value is not None and not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", value):
            raise ValueError(
                "Table name must contain only letters, numbers, and underscores, and start with a letter or underscore"
            )
        return value


class TableImportResponse(BaseModel):
    """Response model for importing a table from CSV."""

    table: TableRead
    rows_inserted: int
    column_mapping: list[InferredColumn]
