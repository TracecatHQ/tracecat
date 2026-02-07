"""Cursor-based pagination utilities for Tracecat."""

import base64
import json
from datetime import datetime
from typing import TypeVar
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T")


class CursorPaginationParams(BaseModel):
    """Parameters for cursor-based pagination."""

    limit: int = Field(default=20, ge=1, le=200, description="Maximum items per page")
    cursor: str | None = Field(default=None, description="Cursor for pagination")
    reverse: bool = Field(default=False, description="Reverse pagination direction")


class CursorPaginatedResponse[T](BaseModel):
    """Response format for cursor-based pagination."""

    items: list[T]
    next_cursor: str | None = Field(default=None, description="Cursor for next page")
    prev_cursor: str | None = Field(
        default=None, description="Cursor for previous page"
    )
    has_more: bool = Field(default=False, description="Whether more items exist")
    has_previous: bool = Field(
        default=False, description="Whether previous items exist"
    )
    total_estimate: int | None = Field(
        default=None, description="Estimated total count from table statistics"
    )


class CursorData(BaseModel):
    """Internal structure for cursor data."""

    id: str
    sort_column: str | None = Field(
        default=None, description="Column name being sorted (for sort-aware pagination)"
    )
    sort_value: datetime | str | int | float | None = Field(
        default=None,
        description="Serialized value of sort column for the cursor row",
    )

    @field_validator("sort_value", mode="before")
    @classmethod
    def parse_datetime_string(
        cls, v: str | int | float | None
    ) -> datetime | str | int | float | None:
        """Try to parse ISO datetime strings back to datetime objects."""
        if isinstance(v, str):
            try:
                # Attempt to parse as ISO datetime
                return datetime.fromisoformat(v.replace("Z", "+00:00"))
            except ValueError:
                # Not a datetime string, return as-is
                return v
        return v


class BaseCursorPaginator:
    """Base class for cursor-based pagination."""

    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def encode_cursor(
        id: UUID | str,
        sort_column: str | None = None,
        sort_value: str | int | float | datetime | None = None,
    ) -> str:
        """Encode a cursor from timestamp and ID, optionally with sort column info.
        Args:
            id: The row ID (used as tie-breaker for stable pagination)
            sort_column: Optional name of the column being sorted (e.g., "priority", "severity", "created_at")
            sort_value: Optional value of the sort column. Should be datetime for datetime columns.
        """
        cursor_data = CursorData(
            id=str(id),
            sort_column=sort_column,
            sort_value=sort_value,
        )
        json_str = cursor_data.model_dump_json()
        return base64.urlsafe_b64encode(json_str.encode()).decode()

    @staticmethod
    def decode_cursor(cursor: str) -> CursorData:
        """Decode a cursor to timestamp and ID."""
        try:
            json_str = base64.urlsafe_b64decode(cursor.encode()).decode()
            data = json.loads(json_str)
            return CursorData.model_validate(data)
        except Exception as e:
            raise ValueError(f"Invalid cursor format: {e}") from e

    async def get_table_row_estimate(
        self, table_name: str, schema_name: str = "public"
    ) -> int | None:
        """Get estimated row count from PostgreSQL table statistics.

        Args:
            table_name: Name of the table
            schema_name: Schema name (default: public)

        Returns:
            Estimated row count or None if unavailable
        """
        try:
            # Create a table object for pg_stat_user_tables
            pg_stat_user_tables = sa.table(
                "pg_stat_user_tables",
                sa.column("n_live_tup", sa.BigInteger),
                sa.column("schemaname", sa.String),
                sa.column("relname", sa.String),
            )

            # Build the query using SQLAlchemy ORM constructs
            stmt = sa.select(
                pg_stat_user_tables.c.n_live_tup.cast(sa.BigInteger).label("estimate")
            ).where(
                sa.and_(
                    pg_stat_user_tables.c.schemaname == schema_name,
                    pg_stat_user_tables.c.relname == table_name,
                )
            )

            conn = await self.session.connection()
            result = await conn.execute(stmt)
            row = result.first()
            return row[0] if row and row[0] is not None else None
        except Exception:
            # Fallback to pg_class if pg_stat_user_tables doesn't have data
            try:
                # Build the query using SQLAlchemy ORM constructs
                pg_class = sa.table(
                    "pg_class",
                    sa.column("reltuples", sa.BigInteger),
                    sa.column("relnamespace", sa.BigInteger),
                )

                pg_namespace = sa.table(
                    "pg_namespace",
                    sa.column("oid", sa.BigInteger),
                    sa.column("nspname", sa.String),
                )

                stmt = (
                    sa.select(
                        pg_class.c.reltuples.cast(sa.BigInteger).label("estimate")
                    )
                    .select_from(
                        pg_class.join(
                            pg_namespace, pg_namespace.c.oid == pg_class.c.relnamespace
                        )
                    )
                    .where(
                        sa.and_(
                            pg_class.c.relname == table_name,
                            pg_namespace.c.nspname == schema_name,
                        )
                    )
                )

                conn = await self.session.connection()
                result = await conn.execute(stmt)
                row = result.first()
                return row[0] if row and row[0] is not None else None
            except Exception:
                return None
