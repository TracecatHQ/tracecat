import re
from typing import Any

import sqlalchemy as sa
from pydantic import BaseModel, Field, field_validator

from tracecat.identifiers import TableColumnID, TableID


class TableColumnRead(BaseModel):
    """Definition for a table column."""

    id: TableColumnID
    name: str
    type: str  # SQL type like 'TEXT', 'INTEGER', etc.
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
    type: str = Field(
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

    @field_validator("type")
    @classmethod
    def validate_sql_type(cls, value: str) -> str:
        """Validate SQL type to prevent injection."""
        if not hasattr(sa.types, value):
            raise ValueError(f"Invalid SQL type: {value}")
        return value


class TableColumnUpdate(BaseModel):
    """Update model for a table column."""

    name: str | None = Field(
        default=None,
        description="The name of the column",
        min_length=1,
        max_length=100,
    )
    type: str | None = Field(
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

    @field_validator("type")
    @classmethod
    def validate_sql_type(cls, value: str) -> str:
        """Validate SQL type to prevent injection."""
        if not hasattr(sa.types, value):
            raise ValueError(f"Invalid SQL type: {value}")
        return value


class TableRowInsert(BaseModel):
    """Insert model for a table row."""

    data: dict[str, Any]


class TableReadMinimal(BaseModel):
    """Read model for a lookup table."""

    id: TableID
    name: str


class TableRead(BaseModel):
    """Read model for a lookup table."""

    id: TableID
    name: str
    columns: list[TableColumnRead]


class TableCreate(BaseModel):
    """Create model for a lookup table."""

    name: str = Field(
        ...,
        description="The name of the lookup table",
        min_length=1,
        max_length=100,
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
    """Update model for a lookup table."""

    name: str | None = Field(
        default=None,
        description="The name of the lookup table",
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
