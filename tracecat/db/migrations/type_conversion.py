"""Type conversion utilities for Entity to Table migration."""

from tracecat.entities.enums import FieldType
from tracecat.tables.enums import SqlType


def field_type_to_sql_type(field_type: FieldType) -> SqlType:
    """Convert Entity FieldType to Table SqlType.
    Args:
        field_type: The Entity field type to convert
    Returns:
        The corresponding SQL type
    Raises:
        ValueError: If the field type is not supported
    """
    mapping: dict[FieldType, SqlType] = {
        FieldType.INTEGER: SqlType.INTEGER,
        FieldType.NUMBER: SqlType.NUMERIC,
        FieldType.TEXT: SqlType.TEXT,
        FieldType.BOOL: SqlType.BOOLEAN,
        FieldType.JSON: SqlType.JSONB,
        FieldType.DATETIME: SqlType.TIMESTAMPTZ,
        FieldType.DATE: SqlType.TIMESTAMP,
        FieldType.SELECT: SqlType.ENUM,
        FieldType.MULTI_SELECT: SqlType.JSONB,
    }

    if field_type not in mapping:
        raise ValueError(f"Unsupported field type: {field_type}")

    return mapping[field_type]
