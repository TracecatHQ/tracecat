from typing import Any

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
