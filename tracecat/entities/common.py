"""Common utilities and validators for custom entities."""

from datetime import date, datetime
from typing import Any

from tracecat.entities.types import FieldType
from tracecat.entities.validation import validate_default_value_type


def serialize_value(value: Any, field_type: FieldType) -> Any:
    """Serialize a value for storage in JSONB.

    Args:
        value: The value to serialize
        field_type: The field type

    Returns:
        JSON-serializable value
    """
    if value is None:
        return None

    if field_type == FieldType.DATE:
        if isinstance(value, date):
            return value.isoformat()
        return value  # Already a string

    elif field_type == FieldType.DATETIME:
        if isinstance(value, datetime):
            return value.isoformat()
        return value  # Already a string

    elif field_type == FieldType.JSON:
        # JSON values are already serializable
        return value

    # Most types are already JSON-serializable
    return value


def validate_and_serialize_default_value(
    value: Any, field_type: FieldType, enum_options: list[str] | None = None
) -> Any:
    """Validate and serialize a default value for storage.

    This function:
    1. Validates using shared validator
    2. Serializes the value for JSONB storage

    Args:
        value: The default value to validate
        field_type: The field type
        enum_options: Options for SELECT/MULTI_SELECT fields

    Returns:
        Serialized value ready for storage

    Raises:
        PydanticCustomError: If validation fails
    """
    if value is None:
        return None

    # Use shared validator
    validated_value = validate_default_value_type(value, field_type, enum_options)

    # Serialize for storage
    return serialize_value(validated_value, field_type)
