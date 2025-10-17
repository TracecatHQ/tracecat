import re
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import orjson
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from tracecat.tables.enums import SqlType


def is_valid_sql_type(type: str | SqlType) -> bool:
    """Check if the type is a valid SQL type for user-defined columns."""
    try:
        sql_type = SqlType(type)
    except ValueError:
        return False
    # Plain TIMESTAMP is only supported for legacy/system-managed columns.
    return sql_type is not SqlType.TIMESTAMP


def coerce_to_utc_datetime(value: Any) -> datetime:
    """Convert supported inputs into a timezone-aware UTC datetime."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = f"{text[:-1]}+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError as exc:
            raise TypeError(f"Invalid ISO datetime string: {value!r}") from exc
    elif isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(value, tz=UTC)
    else:
        raise TypeError(
            f"Unsupported value for TIMESTAMP/TIMESTAMPTZ column: {type(value).__name__}"
        )

    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def handle_default_value(type: SqlType, default: Any) -> str:
    """Handle converting default values to SQL-compatible strings based on type.

    SECURITY NOTICE: Only used in a SQL DDL statement where parameter binding is not supported.

    Args:
        type: The SQL type to format the default value for
        default: The default value to format

    Returns:
        A properly escaped and formatted SQL literal string

    Raises:
        ValueError: If the SQL type is not supported
    """
    match type:
        case SqlType.JSONB:
            # For JSONB, ensure default is properly quoted and cast
            default_value = f"'{default}'::jsonb"
        case SqlType.TEXT:
            # For string types, ensure proper quoting
            default_value = f"'{default}'"
        case SqlType.TIMESTAMP | SqlType.TIMESTAMPTZ:
            # For timestamp with timezone, ensure proper format and quoting
            dt = coerce_to_utc_datetime(default)
            default_value = f"'{dt.isoformat()}'::timestamptz"
        case SqlType.BOOLEAN:
            # For boolean, convert to lowercase string representation
            default_value = str(bool(default)).lower()
        case SqlType.INTEGER | SqlType.NUMERIC:
            # For numeric types, use the value directly
            default_value = str(default)
        case SqlType.UUID:
            # For UUID, ensure proper quoting
            default_value = f"'{default}'::uuid"
        case _:
            raise ValueError(f"Unsupported SQL type for default value: {type}")
    return default_value


def to_sql_clause(value: Any, name: str, sql_type: SqlType) -> sa.BindParameter:
    """Convert a value to a SQL-compatible string based on type.

    Args:
        value: The value to convert to SQL format
        type: The SQL type to convert to

    Returns:
        A SQL-compatible string representation of the value

    Raises:
        ValueError: If the SQL type is not supported
    """
    match sql_type:
        case SqlType.JSONB:
            return sa.bindparam(key=name, value=value, type_=JSONB)
        case SqlType.TEXT:
            return sa.bindparam(key=name, value=str(value), type_=sa.String)
        case SqlType.TIMESTAMP | SqlType.TIMESTAMPTZ:
            coerced = None if value is None else coerce_to_utc_datetime(value)
            return sa.bindparam(
                key=name, value=coerced, type_=sa.TIMESTAMP(timezone=True)
            )
        case SqlType.BOOLEAN:
            # Allow bool, 1, 0 as valid boolean values
            match str(value).lower():
                case "true" | "1":
                    bool_value = True
                case "false" | "0":
                    bool_value = False
                case _:
                    raise TypeError(
                        f"Expected bool or 0/1, got {type(value).__name__}: {value}"
                    )
            return sa.bindparam(key=name, value=bool_value, type_=sa.Boolean)
        case SqlType.INTEGER:
            return sa.bindparam(key=name, value=value, type_=sa.Integer)
        case SqlType.NUMERIC:
            return sa.bindparam(key=name, value=value, type_=sa.Numeric)
        case SqlType.UUID:
            return sa.bindparam(key=name, value=value, type_=sa.UUID)
        case _:
            raise ValueError(f"Unsupported SQL type for value conversion: {type}")


def parse_postgres_default(default_value: str | None) -> str | None:
    """Parse PostgreSQL default value expressions to extract the actual value.

    PostgreSQL stores default values as SQL expressions with type casts like:
    - 'attack'::text -> attack
    - 0::integer -> 0
    - true::boolean -> true
    - '2024-01-01'::timestamp -> 2024-01-01

    Args:
        default_value: The raw default value from PostgreSQL column reflection

    Returns:
        The parsed default value without type casts, or None if input is None
    """
    if default_value is None:
        return None

    # Remove a trailing PostgreSQL type cast suffix (e.g., ::text, ::timestamp)
    # Only strip if the cast appears at the end of the expression to avoid
    # breaking values like nextval('seq'::regclass)
    cast_suffix_pattern = re.compile(r"::[A-Za-z_][\w\. ]*(\[\])?\s*$")
    # Strip multiple trailing casts if present (e.g., 'x'::text::text)
    while cast_suffix_pattern.search(default_value):
        default_value = cast_suffix_pattern.sub("", default_value)

    # Remove surrounding quotes if present
    if default_value.startswith("'") and default_value.endswith("'"):
        default_value = default_value[1:-1]

    return default_value


def convert_value(value: str | None, type: SqlType) -> Any:
    if value is None:
        return None
    try:
        match type:
            case SqlType.INTEGER:
                return int(value)
            case SqlType.NUMERIC:
                return float(value)
            case SqlType.BOOLEAN:
                match value.lower():
                    case "true" | "1":
                        return True
                    case "false" | "0":
                        return False
                    case _:
                        raise ValueError(f"Invalid boolean value: {value}")
            case SqlType.JSONB:
                return orjson.loads(value)
            case SqlType.TEXT:
                return str(value)
            case SqlType.TIMESTAMP | SqlType.TIMESTAMPTZ:
                return coerce_to_utc_datetime(value)
            case SqlType.UUID:
                return UUID(value)
            case _:
                raise ValueError(f"Unsupported SQL type for value conversion: {type}")
    except Exception as e:
        raise TypeError(
            f"Cannot convert value {value!r} to {type.__class__.__name__} {type.value}"
        ) from e
