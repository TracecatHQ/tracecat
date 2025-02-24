from datetime import datetime
from typing import Any
from uuid import UUID

import orjson
import sqlalchemy as sa

from tracecat.db.schemas import TableColumn
from tracecat.tables.enums import SqlType


def is_valid_sql_type(type: str) -> bool:
    """Check if the type is a valid SQL type."""
    return type in SqlType


def handle_default_value(type: SqlType, default: Any) -> str:
    """Handle converting default values to SQL-compatible strings based on type."""
    match type:
        case SqlType.JSONB:
            # For JSONB, ensure default is properly quoted and cast
            default_value = f"'{default}'::jsonb"
        case SqlType.TEXT | SqlType.VARCHAR:
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
        case SqlType.INTEGER | SqlType.BIGINT | SqlType.DECIMAL:
            # For numeric types, use the value directly
            default_value = str(default)
        case SqlType.UUID:
            # For UUID, ensure proper quoting
            default_value = f"'{default}'::uuid"
        case _:
            raise ValueError(f"Unsupported SQL type for default value: {type}")
    return default_value


def to_sql_clause(value: Any, column: TableColumn) -> sa.TextClause:
    """Convert a value to a SQL-compatible string based on type.

    Args:
        value: The value to convert to SQL format
        type: The SQL type to convert to

    Returns:
        A SQL-compatible string representation of the value

    Raises:
        ValueError: If the SQL type is not supported
    """
    name = column.name
    match SqlType(column.type):
        case SqlType.JSONB:
            # orjson handles serialization of complex types better than json
            value = orjson.dumps(value).decode()
            return sa.text(f"(:{name})::jsonb").bindparams(**{name: value})
        case SqlType.TEXT | SqlType.VARCHAR:
            return sa.text(f"(:{name})").bindparams(**{name: str(value)})
        case SqlType.TIMESTAMP:
            return sa.text(f"(:{name})::timestamp").bindparams(**{name: str(value)})
        case SqlType.TIMESTAMPTZ:
            return sa.text(f"(:{name})::timestamptz").bindparams(**{name: str(value)})
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
            return sa.text(f"(:{name})::boolean").bindparams(**{name: bool_value})
        case SqlType.INTEGER:
            return sa.text(f"(:{name})::integer").bindparams(**{name: str(value)})
        case SqlType.BIGINT:
            return sa.text(f"(:{name})::bigint").bindparams(**{name: str(value)})
        case SqlType.DECIMAL:
            return sa.text(f"(:{name})::decimal").bindparams(**{name: str(value)})
        case SqlType.UUID:
            return sa.text(f"(:{name})::uuid").bindparams(**{name: str(value)})
        case _:
            raise ValueError(f"Unsupported SQL type for value conversion: {type}")


def convert_value(value: str, type: SqlType) -> Any:
    try:
        match type:
            case SqlType.INTEGER | SqlType.BIGINT:
                return int(value)
            case SqlType.DECIMAL:
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
            case SqlType.TEXT | SqlType.VARCHAR:
                return str(value)
            case SqlType.TIMESTAMP | SqlType.TIMESTAMPTZ:
                return datetime.fromisoformat(value)
            case SqlType.UUID:
                return UUID(value)
            case _:
                raise ValueError(f"Unsupported SQL type for value conversion: {type}")
    except Exception as e:
        raise TypeError(
            f"Cannot convert value {value!r} to {type.__class__.__name__} {type.value}"
        ) from e
