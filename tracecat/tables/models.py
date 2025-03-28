import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tracecat.identifiers import TableColumnID, TableID, TableRowID
from tracecat.tables.enums import SqlType


class TableColumnRead(BaseModel):
    """Definition for a table column."""

    id: TableColumnID
    name: str
    type: SqlType
    nullable: bool = True
    default: Any | None = None


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


class TableRowRead(BaseModel):
    """Read model for a table row."""

    model_config = ConfigDict(extra="allow")
    id: TableRowID
    created_at: datetime
    updated_at: datetime


class TableRowInsert(BaseModel):
    """Insert model for a table row."""

    data: dict[str, Any]
    upsert: bool = False


class TableRowInsertBatch(BaseModel):
    """Request body for batch inserting rows."""

    rows: list[dict[str, Any]]


class TableRowInsertBatchResponse(BaseModel):
    """Response for batch insert operation."""

    rows_inserted: int


class TableReadMinimal(BaseModel):
    """Read model for a table."""

    id: TableID
    name: str


class TableRead(BaseModel):
    """Read model for a table."""

    id: TableID
    name: str
    columns: list[TableColumnRead]


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
