import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import orjson
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from tracecat.tables.enums import SqlType


def is_valid_sql_type(type: str) -> bool:
    """Check if the type is a valid SQL type."""
    return type in SqlType


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
        case SqlType.TIMESTAMP:
            # For timestamp, ensure proper format and quoting
            default_value = f"'{default}'::timestamp"
        case SqlType.TIMESTAMPTZ:
            # For timestamp with timezone, ensure proper format and quoting
            default_value = f"'{default}'::timestamptz"
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


def _normalize_isoformat(value: str) -> str:
    """Normalize ISO datetime strings for Python's datetime parser."""

    normalized_value = value.strip()
    if normalized_value.endswith("Z"):
        # Python's datetime.fromisoformat doesn't understand the "Z" suffix
        normalized_value = normalized_value[:-1] + "+00:00"
    return normalized_value


def _coerce_datetime(value: Any) -> datetime:
    """Coerce a value into a datetime instance."""

    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(_normalize_isoformat(value))
    raise TypeError(
        "Expected datetime or ISO 8601 string, "
        f"got {type(value).__name__}: {value!r}"
    )


def ensure_tzaware_datetime(value: Any) -> datetime:
    """Ensure a datetime value is timezone-aware, assuming UTC when missing."""

    dt_value = _coerce_datetime(value)
    if dt_value.tzinfo is None:
        return dt_value.replace(tzinfo=timezone.utc)
    return dt_value


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
        case SqlType.TIMESTAMP:
            datetime_value = (
                None
                if value is None
                else _coerce_datetime(value)
                if isinstance(value, (datetime, str))
                else value
            )
            return sa.bindparam(
                key=name,
                value=datetime_value,
                type_=sa.TIMESTAMP,
            )
        case SqlType.TIMESTAMPTZ:
            datetime_value = (
                None if value is None else ensure_tzaware_datetime(value)
            )
            return sa.bindparam(
                key=name,
                value=datetime_value,
                type_=sa.TIMESTAMP(timezone=True),
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


def convert_value(value: str, type: SqlType) -> Any:
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
            case SqlType.TIMESTAMP:
                return _coerce_datetime(value)
            case SqlType.TIMESTAMPTZ:
                return ensure_tzaware_datetime(value)
            case SqlType.UUID:
                return UUID(value)
            case _:
                raise ValueError(f"Unsupported SQL type for value conversion: {type}")
    except Exception as e:
        raise TypeError(
            f"Cannot convert value {value!r} to {type.__class__.__name__} {type.value}"
        ) from e
