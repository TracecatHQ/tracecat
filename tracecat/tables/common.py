from typing import Any

import orjson
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

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
        case SqlType.TEXT:
            # For string types, ensure proper quoting
            default_value = f"'{default}'"
        case SqlType.BOOLEAN:
            # For boolean, convert to lowercase string representation
            default_value = str(bool(default)).lower()
        case SqlType.INTEGER | SqlType.DECIMAL:
            # For numeric types, use the value directly
            default_value = str(default)
        case _:
            raise ValueError(f"Unsupported SQL type for default value: {type}")
    return default_value


def to_sql_clause(value: Any, column: TableColumn) -> sa.BindParameter:
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
            return sa.bindparam(key=name, value=value, type_=JSONB)
        case SqlType.TEXT:
            return sa.bindparam(key=name, value=str(value), type_=sa.String)
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
        case SqlType.DECIMAL:
            return sa.bindparam(key=name, value=value, type_=sa.Numeric)
        case _:
            raise ValueError(f"Unsupported SQL type for value conversion: {type}")


def convert_value(value: str, type: SqlType) -> Any:
    try:
        match type:
            case SqlType.INTEGER:
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
            case SqlType.TEXT:
                return str(value)
            case _:
                raise ValueError(f"Unsupported SQL type for value conversion: {type}")
    except Exception as e:
        raise TypeError(
            f"Cannot convert value {value!r} to {type.__class__.__name__} {type.value}"
        ) from e
