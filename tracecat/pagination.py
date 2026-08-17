"""Cursor-based pagination utilities for Tracecat.

The primary API is :func:`paginate`. Callers provide a scoped SQLAlchemy
``Select``, an explicit total ordering, and :class:`PageParams`; the module owns
cursor encoding, seek predicates, navigation direction, and page construction.

The older ``Cursor*`` types remain temporarily for existing callers. New code
should use ``PageParams``, ``Page``, and ``paginate``.
"""

import base64
import hashlib
import hmac
import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, StrEnum
from itertools import pairwise
from typing import Any, Self, TypeVar, cast, overload
from uuid import UUID

import orjson
import sqlalchemy as sa
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    PydanticUserError,
    TypeAdapter,
    ValidationError,
    field_validator,
)
from pydantic_core import PydanticSerializationError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import ColumnElement, UnaryExpression
from sqlalchemy.sql.selectable import Select

from tracecat import config
from tracecat.auth.secrets import get_signing_secret
from tracecat.exceptions import TracecatValidationError

T = TypeVar("T")

_CURSOR_VERSION = 1
_CURSOR_SIGNATURE_CONTEXT = b"tracecat.pagination.cursor.v1\0"
_MAX_CURSOR_LENGTH = 8192
_JSON_VALUE_ADAPTER = TypeAdapter(JsonValue)
_FINGERPRINT_JSON_OPTIONS = (
    orjson.OPT_NON_STR_KEYS | orjson.OPT_SORT_KEYS | orjson.OPT_UTC_Z
)


class PageParams(BaseModel):
    """Parameters for retrieving one page.

    A cursor returned in either direction is a complete continuation token;
    callers do not need to provide a separate direction flag.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    limit: int = Field(
        default=config.TRACECAT__LIMIT_DEFAULT,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
        description="Maximum items per page",
    )
    cursor: str | None = Field(
        default=None,
        max_length=_MAX_CURSOR_LENGTH,
        description="Opaque continuation token",
    )


class Page[T](BaseModel):
    """A page of canonically ordered items."""

    model_config = ConfigDict(frozen=True)

    items: list[T]
    next_cursor: str | None = Field(default=None, description="Next-page cursor")
    prev_cursor: str | None = Field(default=None, description="Previous-page cursor")

    @property
    def has_more(self) -> bool:
        """Return whether another page exists in the forward direction."""
        return self.next_cursor is not None

    @property
    def has_previous(self) -> bool:
        """Return whether another page exists in the backward direction."""
        return self.prev_cursor is not None


class PaginationErrorCode(StrEnum):
    """Machine-readable pagination failures caused by request cursors."""

    INVALID_CURSOR = "invalid_cursor"
    CURSOR_MISMATCH = "cursor_mismatch"
    UNSUPPORTED_CURSOR_VERSION = "unsupported_cursor_version"


class PaginationError(TracecatValidationError):
    """A user-facing pagination cursor error."""

    def __init__(self, message: str, *, code: PaginationErrorCode) -> None:
        super().__init__(message, detail={"code": code.value})
        self.code = code


class PaginationConfigurationError(ValueError):
    """A developer error in a paginated query definition."""


class _Direction(StrEnum):
    FORWARD = "forward"
    BACKWARD = "backward"


class _Boundary(StrEnum):
    EXCLUSIVE = "exclusive"
    INCLUSIVE = "inclusive"


class _SortDirection(StrEnum):
    ASC = "asc"
    DESC = "desc"


class _NullPlacement(StrEnum):
    FIRST = "first"
    LAST = "last"


class _CursorPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int
    direction: _Direction
    boundary: _Boundary = _Boundary.EXCLUSIVE
    query_fingerprint: str
    values: list[JsonValue]


@dataclass(frozen=True, slots=True)
class _OrderKey:
    expression: ColumnElement[Any]
    direction: _SortDirection
    nulls: _NullPlacement
    adapter: TypeAdapter[Any]

    def clause(self) -> ColumnElement[Any]:
        """Build an explicit SQL ordering clause."""
        if self.direction is _SortDirection.ASC:
            clause = self.expression.asc()
        else:
            clause = self.expression.desc()
        if self.nulls is _NullPlacement.FIRST:
            return clause.nulls_first()
        return clause.nulls_last()

    def reversed(self) -> Self:
        """Return the exact inverse ordering for backward retrieval."""
        direction = (
            _SortDirection.DESC
            if self.direction is _SortDirection.ASC
            else _SortDirection.ASC
        )
        nulls = (
            _NullPlacement.LAST
            if self.nulls is _NullPlacement.FIRST
            else _NullPlacement.FIRST
        )
        return type(self)(
            expression=self.expression,
            direction=direction,
            nulls=nulls,
            adapter=self.adapter,
        )


@dataclass(frozen=True, slots=True)
class _PageRow[T]:
    item: T
    cursor_values: tuple[object, ...]


def _normalize_order(expression: ColumnElement[Any]) -> _OrderKey:
    """Extract direction and null placement from an SQLAlchemy order clause."""
    current = expression
    direction: _SortDirection | None = None
    nulls: _NullPlacement | None = None

    while isinstance(current, UnaryExpression):
        if current.modifier is operators.asc_op:
            if direction is not None:
                raise PaginationConfigurationError(
                    "Pagination ordering contains more than one direction modifier"
                )
            direction = _SortDirection.ASC
            current = current.element
            continue
        if current.modifier is operators.desc_op:
            if direction is not None:
                raise PaginationConfigurationError(
                    "Pagination ordering contains more than one direction modifier"
                )
            direction = _SortDirection.DESC
            current = current.element
            continue
        if current.modifier is operators.nulls_first_op:
            if nulls is not None:
                raise PaginationConfigurationError(
                    "Pagination ordering contains more than one NULLS modifier"
                )
            nulls = _NullPlacement.FIRST
            current = current.element
            continue
        if current.modifier is operators.nulls_last_op:
            if nulls is not None:
                raise PaginationConfigurationError(
                    "Pagination ordering contains more than one NULLS modifier"
                )
            nulls = _NullPlacement.LAST
            current = current.element
            continue
        break

    if direction is None:
        raise PaginationConfigurationError(
            "Every pagination ordering expression must specify .asc() or .desc()"
        )

    try:
        value_type = current.type.python_type
    except (AttributeError, NotImplementedError) as exc:
        raise PaginationConfigurationError(
            f"Pagination ordering expression {current!s} has no Python value type"
        ) from exc
    try:
        adapter = TypeAdapter(value_type)
    except (PydanticUserError, TypeError) as exc:
        raise PaginationConfigurationError(
            f"Pagination ordering expression {current!s} has an unsupported value type"
        ) from exc

    return _OrderKey(
        expression=current,
        direction=direction,
        nulls=nulls or _NullPlacement.LAST,
        adapter=adapter,
    )


def _normalize_ordering(
    order_by: tuple[ColumnElement[Any], ...],
) -> tuple[_OrderKey, ...]:
    if not order_by:
        raise PaginationConfigurationError(
            "Pagination requires a deterministic total ordering"
        )
    return tuple(_normalize_order(expression) for expression in order_by)


def _validate_statement(statement: Select[Any]) -> None:
    """Reject query state that would compete with paginator-owned clauses."""
    if statement._order_by_clauses:
        raise PaginationConfigurationError(
            "Pass ordering through paginate(order_by=...), not Select.order_by()"
        )
    if (
        statement._limit_clause is not None
        or statement._offset_clause is not None
        or statement._fetch_clause is not None
    ):
        raise PaginationConfigurationError(
            "A paginated Select cannot already contain LIMIT, OFFSET, or FETCH"
        )


def _validate_page(page: PageParams) -> None:
    """Defend pagination limits even if Pydantic validation was bypassed."""
    if (
        not config.TRACECAT__LIMIT_MIN
        <= page.limit
        <= config.TRACECAT__LIMIT_CURSOR_MAX
    ):
        raise PaginationConfigurationError(
            "Page limit is outside the configured cursor-pagination bounds"
        )


def _fingerprint_json_default(value: object) -> object:
    """Return a stable JSON representation for uncommon SQL bind values."""
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=repr)
    if isinstance(value, Enum):
        return (type(value).__module__, type(value).__qualname__, value.value)
    return (type(value).__module__, type(value).__qualname__, str(value))


def _fingerprint_value_sort_key(value: object) -> bytes:
    """Return a deterministic ordering key for an expanding bind value."""
    return orjson.dumps(
        value,
        default=_fingerprint_json_default,
        option=_FINGERPRINT_JSON_OPTIONS,
    )


def _normalize_expanding_bind(value: object) -> object:
    """Canonicalize the order-insensitive values of an SQL ``IN`` clause."""
    if isinstance(value, (list, tuple, set, frozenset)):
        return sorted(value, key=_fingerprint_value_sort_key)
    return value


def _query_fingerprint(
    session: AsyncSession,
    statement: Select[Any],
    ordering: tuple[_OrderKey, ...],
) -> str:
    """Fingerprint the query, bind values, and canonical ordering."""
    ordered_statement = statement.order_by(*(key.clause() for key in ordering))
    compiled = ordered_statement.compile(dialect=session.get_bind().dialect)
    expanding_parameter_names = {
        compiled.bind_names[parameter] for parameter in compiled.post_compile_params
    }
    fingerprint_parameters: list[tuple[str, object]] = []
    for name, value in sorted(compiled.params.items()):
        if name in expanding_parameter_names:
            value = _normalize_expanding_bind(value)
        fingerprint_parameters.append((name, value))

    parameters_json = orjson.dumps(
        fingerprint_parameters,
        default=_fingerprint_json_default,
        option=_FINGERPRINT_JSON_OPTIONS,
    )
    execution_options_json = orjson.dumps(
        sorted(statement.get_execution_options().items()),
        default=_fingerprint_json_default,
        option=_FINGERPRINT_JSON_OPTIONS,
    )
    digest = hashlib.sha256()
    digest.update(str(compiled).encode())
    digest.update(b"\0")
    digest.update(parameters_json)
    digest.update(b"\0")
    digest.update(execution_options_json)
    return digest.hexdigest()


def _urlsafe_b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _urlsafe_b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(
        (value + padding).encode("ascii"), altchars=b"-_", validate=True
    )


def _cursor_signature(payload: bytes, *, secret: bytes) -> bytes:
    return hmac.new(
        secret,
        _CURSOR_SIGNATURE_CONTEXT + payload,
        digestmod=hashlib.sha256,
    ).digest()


def _serialize_cursor_values(
    values: tuple[object, ...],
    ordering: tuple[_OrderKey, ...],
) -> list[JsonValue]:
    serialized: list[JsonValue] = []
    try:
        for value, key in zip(values, ordering, strict=True):
            if value is None:
                serialized.append(None)
                continue
            if isinstance(value, float) and not math.isfinite(value):
                if math.isnan(value):
                    json_value = "NaN"
                elif value > 0:
                    json_value = "Infinity"
                else:
                    json_value = "-Infinity"
            else:
                json_value = key.adapter.dump_python(value, mode="json")
            serialized.append(_JSON_VALUE_ADAPTER.validate_python(json_value))
    except (PydanticSerializationError, TypeError, ValueError, ValidationError) as exc:
        raise PaginationConfigurationError(
            "Pagination ordering produced a cursor value that cannot be serialized"
        ) from exc
    return serialized


def _deserialize_cursor_values(
    values: list[JsonValue],
    ordering: tuple[_OrderKey, ...],
) -> tuple[object, ...]:
    if len(values) != len(ordering):
        raise PaginationError(
            "Invalid pagination cursor",
            code=PaginationErrorCode.INVALID_CURSOR,
        )

    deserialized: list[object] = []
    try:
        for value, key in zip(values, ordering, strict=True):
            if value is None:
                deserialized.append(None)
                continue
            deserialized.append(key.adapter.validate_python(value))
    except (TypeError, ValueError, ValidationError) as exc:
        raise PaginationError(
            "Invalid pagination cursor",
            code=PaginationErrorCode.INVALID_CURSOR,
        ) from exc
    return tuple(deserialized)


def _encode_page_cursor(
    *,
    direction: _Direction,
    boundary: _Boundary = _Boundary.EXCLUSIVE,
    query_fingerprint: str,
    values: tuple[object, ...],
    ordering: tuple[_OrderKey, ...],
    secret: bytes,
) -> str:
    payload = (
        _CursorPayload(
            version=_CURSOR_VERSION,
            direction=direction,
            boundary=boundary,
            query_fingerprint=query_fingerprint,
            values=_serialize_cursor_values(values, ordering),
        )
        .model_dump_json()
        .encode()
    )
    signature = _cursor_signature(payload, secret=secret)
    cursor = f"{_urlsafe_b64encode(payload)}.{_urlsafe_b64encode(signature)}"
    if len(cursor) > _MAX_CURSOR_LENGTH:
        raise PaginationConfigurationError(
            "Encoded pagination cursor exceeds the maximum length; "
            "use compact ordering keys"
        )
    return cursor


def _decode_page_cursor(
    cursor: str,
    *,
    query_fingerprint: str,
    ordering: tuple[_OrderKey, ...],
    secret: bytes,
) -> tuple[_Direction, _Boundary, tuple[object, ...]]:
    try:
        if len(cursor) > _MAX_CURSOR_LENGTH:
            raise ValueError("Cursor is too long")
        payload_part, signature_part = cursor.split(".", maxsplit=1)
        payload = _urlsafe_b64decode(payload_part)
        signature = _urlsafe_b64decode(signature_part)
        expected_signature = _cursor_signature(payload, secret=secret)
        if not hmac.compare_digest(signature, expected_signature):
            raise ValueError("Cursor signature does not match")
        decoded = _CursorPayload.model_validate_json(payload)
    except (TypeError, ValueError, ValidationError) as exc:
        raise PaginationError(
            "Invalid pagination cursor",
            code=PaginationErrorCode.INVALID_CURSOR,
        ) from exc

    if decoded.version != _CURSOR_VERSION:
        raise PaginationError(
            "Unsupported pagination cursor version",
            code=PaginationErrorCode.UNSUPPORTED_CURSOR_VERSION,
        )
    if decoded.query_fingerprint != query_fingerprint:
        raise PaginationError(
            "Pagination cursor does not match the current query",
            code=PaginationErrorCode.CURSOR_MISMATCH,
        )
    return (
        decoded.direction,
        decoded.boundary,
        _deserialize_cursor_values(decoded.values, ordering),
    )


def _strictly_after(key: _OrderKey, value: object) -> ColumnElement[bool]:
    """Build the strict comparison after one value in the given ordering."""
    expression = key.expression
    if value is None:
        if key.nulls is _NullPlacement.FIRST:
            return expression.is_not(None)
        return sa.false()

    comparison = (
        expression > value
        if key.direction is _SortDirection.ASC
        else expression < value
    )
    if key.nulls is _NullPlacement.LAST:
        return sa.or_(comparison, expression.is_(None))
    return comparison


def _seek_predicate(
    ordering: tuple[_OrderKey, ...],
    values: tuple[object, ...],
    *,
    boundary: _Boundary,
) -> ColumnElement[bool]:
    """Build a lexicographic predicate after the cursor values."""
    if len(ordering) != len(values):
        raise PaginationConfigurationError(
            "Cursor values do not match pagination ordering"
        )

    branches: list[ColumnElement[bool]] = []
    equal_prefix: list[ColumnElement[bool]] = []
    for key, value in zip(ordering, values, strict=True):
        branches.append(sa.and_(*equal_prefix, _strictly_after(key, value)))
        equal_prefix.append(key.expression.is_not_distinct_from(value))
    if boundary is _Boundary.INCLUSIVE:
        branches.append(sa.and_(*equal_prefix))
    return sa.or_(*branches)


@overload
async def paginate[T](
    session: AsyncSession,
    statement: Select[tuple[T]],
    *,
    page: PageParams,
    order_by: tuple[ColumnElement[Any], ...],
    row_factory: None = None,
) -> Page[T]: ...


@overload
async def paginate[T](
    session: AsyncSession,
    statement: Select[Any],
    *,
    page: PageParams,
    order_by: tuple[ColumnElement[Any], ...],
    row_factory: Callable[[tuple[object, ...]], T],
) -> Page[T]: ...


async def paginate[T](
    session: AsyncSession,
    statement: Select[Any],
    *,
    page: PageParams,
    order_by: tuple[ColumnElement[Any], ...],
    row_factory: Callable[[tuple[object, ...]], T] | None = None,
) -> Page[T]:
    """Execute a stable keyset-paginated SQLAlchemy query.

    The statement must not already contain ``ORDER BY``, ``LIMIT``, ``OFFSET``,
    or ``FETCH``. The supplied ordering must end in a key (or key combination)
    that uniquely identifies each logical result row. All ordering expressions
    must use explicit ``.asc()`` or ``.desc()`` modifiers.

    Pagination is stateless rather than snapshot-based. Inserts and deletes do
    not cause offset drift, and a deleted anchor row remains usable, but a row
    whose ordering values change between requests can move across a cursor.
    Prefer immutable ordering keys when traversal consistency is important.

    Args:
        session: Database session used to execute the query.
        statement: Scoped query selecting one logical item per row.
        page: Requested page size and optional continuation token.
        order_by: Complete, deterministic ordering for the result set. Dynamic
            ``sa.column()`` expressions must include their SQLAlchemy type so
            cursor values can be decoded without guessing.
        row_factory: Optional conversion for a multi-column selection. It
            receives only the statement's original selected values; private
            cursor columns are hidden.

    Returns:
        Items in canonical order with opaque next and previous cursors.

    Raises:
        PaginationConfigurationError: The query does not satisfy pagination
            invariants.
        PaginationError: The supplied cursor is invalid or belongs to another
            query.
    """
    _validate_page(page)
    _validate_statement(statement)
    ordering = _normalize_ordering(order_by)
    selected_width = len(statement.column_descriptions)
    if selected_width == 0:
        raise PaginationConfigurationError(
            "A paginated Select must return at least one value"
        )
    if selected_width != 1 and row_factory is None:
        raise PaginationConfigurationError(
            "Multi-column pagination requires a row_factory"
        )

    query_fingerprint = _query_fingerprint(session, statement, ordering)
    secret = get_signing_secret().encode()
    direction = _Direction.FORWARD
    boundary = _Boundary.EXCLUSIVE
    cursor_values: tuple[object, ...] | None = None
    if page.cursor is not None:
        direction, boundary, cursor_values = _decode_page_cursor(
            page.cursor,
            query_fingerprint=query_fingerprint,
            ordering=ordering,
            secret=secret,
        )

    query_ordering = (
        ordering
        if direction is _Direction.FORWARD
        else tuple(key.reversed() for key in ordering)
    )
    cursor_columns = tuple(
        key.expression.label(f"_tracecat_page_key_{index}")
        for index, key in enumerate(ordering)
    )
    query = statement.add_columns(*cursor_columns)
    if cursor_values is not None:
        query = query.where(
            _seek_predicate(
                query_ordering,
                cursor_values,
                boundary=boundary,
            )
        )
    query = query.order_by(*(key.clause() for key in query_ordering)).limit(
        page.limit + 1
    )

    result = await session.execute(query)
    raw_rows = list(result.all())
    raw_cursor_values = [tuple(row)[-len(ordering) :] for row in raw_rows]
    serialized_cursor_values = [
        _serialize_cursor_values(values, ordering) for values in raw_cursor_values
    ]
    duplicate_ordering = any(
        previous_values == current_values or previous_serialized == current_serialized
        for (previous_values, current_values), (
            previous_serialized,
            current_serialized,
        ) in zip(
            pairwise(raw_cursor_values),
            pairwise(serialized_cursor_values),
            strict=True,
        )
    )
    if duplicate_ordering:
        raise PaginationConfigurationError(
            "Pagination ordering is not unique; add a unique tie-breaker"
        )
    has_more_in_direction = len(raw_rows) > page.limit
    raw_rows = raw_rows[: page.limit]

    page_rows: list[_PageRow[T]] = []
    for row in raw_rows:
        values = tuple(row)
        selected_values = values[:selected_width]
        item = (
            row_factory(selected_values)
            if row_factory is not None
            else cast(T, selected_values[0])
        )
        page_rows.append(_PageRow(item=item, cursor_values=values[-len(ordering) :]))

    if direction is _Direction.BACKWARD:
        page_rows.reverse()

    next_cursor: str | None = None
    prev_cursor: str | None = None
    if page_rows:
        if direction is _Direction.FORWARD:
            if has_more_in_direction:
                next_cursor = _encode_page_cursor(
                    direction=_Direction.FORWARD,
                    query_fingerprint=query_fingerprint,
                    values=page_rows[-1].cursor_values,
                    ordering=ordering,
                    secret=secret,
                )
            if page.cursor is not None and boundary is _Boundary.EXCLUSIVE:
                prev_cursor = _encode_page_cursor(
                    direction=_Direction.BACKWARD,
                    query_fingerprint=query_fingerprint,
                    values=page_rows[0].cursor_values,
                    ordering=ordering,
                    secret=secret,
                )
        else:
            if page.cursor is not None and boundary is _Boundary.EXCLUSIVE:
                next_cursor = _encode_page_cursor(
                    direction=_Direction.FORWARD,
                    query_fingerprint=query_fingerprint,
                    values=page_rows[-1].cursor_values,
                    ordering=ordering,
                    secret=secret,
                )
            if has_more_in_direction:
                prev_cursor = _encode_page_cursor(
                    direction=_Direction.BACKWARD,
                    query_fingerprint=query_fingerprint,
                    values=page_rows[0].cursor_values,
                    ordering=ordering,
                    secret=secret,
                )
    elif cursor_values is not None and boundary is _Boundary.EXCLUSIVE:
        # Concurrent deletes can empty a page after its cursor was issued. A
        # one-use inclusive cursor restores the adjacent surviving page without
        # pointing back toward the page now known to be empty.
        if direction is _Direction.FORWARD:
            prev_cursor = _encode_page_cursor(
                direction=_Direction.BACKWARD,
                boundary=_Boundary.INCLUSIVE,
                query_fingerprint=query_fingerprint,
                values=cursor_values,
                ordering=ordering,
                secret=secret,
            )
        else:
            next_cursor = _encode_page_cursor(
                direction=_Direction.FORWARD,
                boundary=_Boundary.INCLUSIVE,
                query_fingerprint=query_fingerprint,
                values=cursor_values,
                ordering=ordering,
                secret=secret,
            )

    return Page(
        items=[row.item for row in page_rows],
        next_cursor=next_cursor,
        prev_cursor=prev_cursor,
    )


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
