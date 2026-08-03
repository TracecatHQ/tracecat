"""Cursor-based pagination utilities for Tracecat."""

import base64
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, TypeVar, overload
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config

T = TypeVar("T")


class CursorPaginationParams(BaseModel):
    """Parameters for cursor-based pagination."""

    limit: int = Field(
        default=config.TRACECAT__LIMIT_DEFAULT,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
        description="Maximum items per page",
    )
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


class InvalidCursorError(ValueError):
    """A cursor that cannot be applied to the query it was submitted with.

    Raised when the cursor's sort column or sort value does not line up with
    the current sort, which happens when a client changes the sort without
    restarting pagination. Dropping the cursor filter instead would silently
    return the first page while still reporting ``has_previous``.

    Subclasses ``ValueError`` so routers surface it as 400, the same way
    ``decode_cursor`` already reports a structurally malformed cursor.
    """


@dataclass(slots=True)
class CursorPage[T]:
    """A single page of keyset-paginated rows in display order."""

    items: list[T]
    next_cursor: str | None
    prev_cursor: str | None
    has_more: bool
    has_previous: bool


def take_cursor_page[T](rows: Sequence[T], *, limit: int) -> tuple[list[T], bool]:
    """Trim an over-fetched keyset scan to a page.

    Args:
        rows: Rows returned by a scan that requested ``limit + 1`` rows.
        limit: Page size requested by the caller.

    Returns:
        The page rows (still in scan order) and whether the scan found more
        rows beyond the page in the direction it was scanning.
    """
    has_more = len(rows) > limit
    return list(rows[:limit]), has_more


def build_cursor_page[T](
    rows: Sequence[T],
    *,
    cursor: str | None,
    reverse: bool,
    has_more: bool,
    encode_cursor: Callable[[T], str],
) -> CursorPage[T]:
    """Derive page items, cursors, and flags from a trimmed keyset scan.

    Reverse pagination scans away from the cursor in the inverted sort order,
    so the caller must invert its ``ORDER BY`` (and the cursor predicate) when
    ``reverse`` is set. This function then puts the rows back into display
    order and swaps the cursors and flags, since the scan's "more rows ahead"
    is the page's "more rows behind".

    Args:
        rows: Page rows in scan order, already trimmed by ``take_cursor_page``.
        cursor: The cursor the scan started from, if any.
        reverse: Whether the scan ran backwards from ``cursor``.
        has_more: Whether the scan found rows beyond the page.
        encode_cursor: Encodes the cursor anchored at a given row.

    Returns:
        The page in display order with cursors and flags in forward semantics.
    """
    items = list(rows)
    next_cursor = encode_cursor(items[-1]) if has_more and items else None
    prev_cursor = encode_cursor(items[0]) if cursor is not None and items else None
    has_previous = cursor is not None

    if reverse:
        items.reverse()
        next_cursor, prev_cursor = prev_cursor, next_cursor
        # In reverse mode "next" walks back toward the page we came from, which
        # is only reachable when this page produced an anchor cursor. Tying it
        # to a bare `cursor is not None` would advertise has_more=True with
        # next_cursor=None on an empty page, enabling a dead pagination control.
        has_more, has_previous = next_cursor is not None, has_more

    return CursorPage(
        items=items,
        next_cursor=next_cursor,
        prev_cursor=prev_cursor,
        has_more=has_more,
        has_previous=has_previous,
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

    @property
    def has_sort_value(self) -> bool:
        """Whether the cursor carries an explicit sort value.

        ``encode_cursor`` always serializes ``sort_value``, so a cursor issued
        by this server has the key even when the anchor row's sort column is
        NULL. An absent key means a cursor from before sort-aware pagination
        (or a hand-built one), which cannot filter anything. Keeping the two
        apart is what lets a nullable sort follow its own ``sort_value: null``
        cursor instead of rejecting it as malformed.
        """
        return "sort_value" in self.model_fields_set


@overload
def validate_cursor_sort_column(
    cursor: CursorData,
    *,
    sort_column: str,
    expected_type: type | tuple[type, ...] | None = ...,
    allow_null: Literal[False] = ...,
) -> str | int | float | datetime: ...


@overload
def validate_cursor_sort_column(
    cursor: CursorData,
    *,
    sort_column: str,
    expected_type: type | tuple[type, ...] | None = ...,
    allow_null: Literal[True],
) -> str | int | float | datetime | None: ...


def validate_cursor_sort_column(
    cursor: CursorData,
    *,
    sort_column: str,
    expected_type: type | tuple[type, ...] | None = None,
    allow_null: bool = False,
) -> str | int | float | datetime | None:
    """Return the cursor's sort value, or raise if it cannot filter this query.

    Args:
        cursor: The decoded cursor.
        sort_column: The sort column of the current request.
        expected_type: Type the sort value must have, when the column's cursor
            representation is narrower than the query's column type (enum sorts
            encode a rank, not the enum value).
        allow_null: Whether the sort column admits NULLs. When set, a cursor
            anchored on a NULL sort value returns ``None`` instead of raising,
            and the caller must pair it with a NULL-aware keyset predicate (see
            ``keyset_filter``). Legacy cursors that omit the field entirely are
            still rejected.

    Raises:
        InvalidCursorError: The cursor belongs to a different sort, carries no
            usable sort value, or carries one of the wrong type.
    """
    if cursor.sort_column != sort_column:
        raise InvalidCursorError(
            f"Cursor was created for sort column {cursor.sort_column!r}, "
            f"but this request sorts by {sort_column!r}. "
            "Restart pagination without a cursor after changing the sort."
        )
    if cursor.sort_value is None:
        if allow_null and cursor.has_sort_value:
            return None
        raise InvalidCursorError(
            f"Cursor is missing a sort value for column {sort_column!r}. "
            "Restart pagination without a cursor."
        )
    if expected_type is not None and not isinstance(cursor.sort_value, expected_type):
        raise InvalidCursorError(
            f"Cursor sort value for column {sort_column!r} has the wrong type. "
            "Restart pagination without a cursor."
        )
    return cursor.sort_value


def keyset_filter(
    sort_col: sa.ColumnElement[Any],
    id_col: sa.ColumnElement[Any],
    *,
    sort_value: str | int | float | datetime | None,
    id_value: Any,
    ascending: bool,
    nullable: bool = True,
) -> sa.ColumnElement[bool]:
    """Build the predicate selecting rows strictly after a cursor anchor.

    Args:
        sort_col: The column (or expression) the query sorts by.
        id_col: The unique tie-breaker column, sorted alongside ``sort_col``.
        sort_value: The anchor row's sort value; ``None`` anchors inside the
            NULL block.
        id_value: The anchor row's tie-breaker value.
        ascending: The direction of the *scan*, not of the requested sort.
            Reverse pagination inverts the scan so ``LIMIT`` keeps the rows
            nearest the cursor, and this predicate must follow that inversion.
        nullable: Whether ``sort_col`` can hold NULLs. Pass ``False`` for
            NOT NULL columns to keep the predicate free of NULL branches; such
            a column can never produce a ``sort_value`` of ``None``.

    NULL placement follows PostgreSQL's defaults, which invert consistently:
    ``ASC`` puts NULLs last and ``DESC`` puts them first, so ``ASC NULLS LAST``
    reversed is exactly ``DESC NULLS FIRST``. An ascending scan therefore
    treats NULL as greater than every value, a descending scan treats it as
    less than every value, and inside the NULL block only ``id_col`` orders
    rows. Callers must order by the matching ``nulls_last()``/``nulls_first()``.
    """
    if ascending:
        if sort_value is None:
            # NULLs sort last, so nothing outside the NULL block follows.
            return sa.and_(sort_col.is_(None), id_col > id_value)
        after = [sort_col > sort_value]
        if nullable:
            # NULLs sort after every value, and `>` is unknown against them.
            after.append(sort_col.is_(None))
        after.append(sa.and_(sort_col == sort_value, id_col > id_value))
        return sa.or_(*after)

    if sort_value is None:
        # NULLs sort first, so every non-NULL row follows the NULL block.
        return sa.or_(
            sort_col.is_not(None),
            sa.and_(sort_col.is_(None), id_col < id_value),
        )
    # NULLs sort before every value, so a non-NULL anchor already excludes
    # them: `<` is unknown against NULL and drops those rows.
    return sa.or_(
        sort_col < sort_value,
        sa.and_(sort_col == sort_value, id_col < id_value),
    )


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
