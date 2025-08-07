"""Common utilities and validators for custom entities."""

from datetime import date, datetime
from typing import Any

from tracecat.entities.types import FieldType


def validate_value_for_type(
    value: Any, field_type: FieldType, settings: dict[str, Any] | None = None
) -> tuple[bool, str | None]:
    """Validate a value against a field type and settings.

    Args:
        value: The value to validate
        field_type: The expected field type
        settings: Optional field-specific settings

    Returns:
        Tuple of (is_valid, error_message)
    """
    settings = settings or {}

    # Handle None values (all fields nullable in v1)
    if value is None:
        return True, None

    try:
        if field_type == FieldType.INTEGER:
            if not isinstance(value, int) or isinstance(value, bool):
                return False, f"Expected integer, got {type(value).__name__}"

            # Check min/max if specified
            if "min" in settings and value < settings["min"]:
                return False, f"Value {value} is below minimum {settings['min']}"
            if "max" in settings and value > settings["max"]:
                return False, f"Value {value} exceeds maximum {settings['max']}"

        elif field_type == FieldType.NUMBER:
            if not isinstance(value, int | float) or isinstance(value, bool):
                return False, f"Expected number, got {type(value).__name__}"

            # Check min/max if specified
            if "min" in settings and value < settings["min"]:
                return False, f"Value {value} is below minimum {settings['min']}"
            if "max" in settings and value > settings["max"]:
                return False, f"Value {value} exceeds maximum {settings['max']}"

        elif field_type == FieldType.TEXT:
            if not isinstance(value, str):
                return False, f"Expected string, got {type(value).__name__}"

            # Check length constraints
            if "min_length" in settings and len(value) < settings["min_length"]:
                return (
                    False,
                    f"Text length {len(value)} is below minimum {settings['min_length']}",
                )
            if "max_length" in settings and len(value) > settings["max_length"]:
                return (
                    False,
                    f"Text length {len(value)} exceeds maximum {settings['max_length']}",
                )

            # Check regex pattern if specified
            if "pattern" in settings:
                import re

                if not re.match(settings["pattern"], value):
                    return False, "Text does not match required pattern"

        elif field_type == FieldType.BOOL:
            if not isinstance(value, bool):
                return False, f"Expected boolean, got {type(value).__name__}"

        elif field_type == FieldType.DATE:
            if isinstance(value, str):
                # Try to parse ISO date string
                try:
                    date.fromisoformat(value)
                except ValueError:
                    return False, "Invalid date format, expected YYYY-MM-DD"
            elif not isinstance(value, date):
                return (
                    False,
                    f"Expected date or ISO date string, got {type(value).__name__}",
                )

        elif field_type == FieldType.DATETIME:
            if isinstance(value, str):
                # Try to parse ISO datetime string
                try:
                    datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError:
                    return False, "Invalid datetime format, expected ISO 8601"
            elif not isinstance(value, datetime):
                return (
                    False,
                    f"Expected datetime or ISO datetime string, got {type(value).__name__}",
                )

        elif field_type == FieldType.SELECT:
            if not isinstance(value, str):
                return (
                    False,
                    f"Expected string for select field, got {type(value).__name__}",
                )

            # Check if value is in allowed options
            if "options" in settings and value not in settings["options"]:
                return False, f"Value '{value}' is not in allowed options"

        elif field_type == FieldType.MULTI_SELECT:
            if not isinstance(value, list):
                return (
                    False,
                    f"Expected list for multi-select field, got {type(value).__name__}",
                )

            # Check all values are strings
            if not all(isinstance(item, str) for item in value):
                return False, "All multi-select values must be strings"

            # Check if all values are in allowed options
            if "options" in settings:
                invalid = [v for v in value if v not in settings["options"]]
                if invalid:
                    return False, f"Values {invalid} are not in allowed options"

        elif field_type == FieldType.ARRAY_TEXT:
            if not isinstance(value, list):
                return (
                    False,
                    f"Expected list for array field, got {type(value).__name__}",
                )

            if not all(isinstance(item, str) for item in value):
                return False, "All array elements must be strings"

            # Check array length constraints
            if "max_items" in settings and len(value) > settings["max_items"]:
                return (
                    False,
                    f"Array length {len(value)} exceeds maximum {settings['max_items']}",
                )

        elif field_type == FieldType.ARRAY_INTEGER:
            if not isinstance(value, list):
                return (
                    False,
                    f"Expected list for array field, got {type(value).__name__}",
                )

            if not all(
                isinstance(item, int) and not isinstance(item, bool) for item in value
            ):
                return False, "All array elements must be integers"

            # Check array length constraints
            if "max_items" in settings and len(value) > settings["max_items"]:
                return (
                    False,
                    f"Array length {len(value)} exceeds maximum {settings['max_items']}",
                )

        elif field_type == FieldType.ARRAY_NUMBER:
            if not isinstance(value, list):
                return (
                    False,
                    f"Expected list for array field, got {type(value).__name__}",
                )

            if not all(
                isinstance(item, int | float) and not isinstance(item, bool)
                for item in value
            ):
                return False, "All array elements must be numbers"

            # Check array length constraints
            if "max_items" in settings and len(value) > settings["max_items"]:
                return (
                    False,
                    f"Array length {len(value)} exceeds maximum {settings['max_items']}",
                )

        else:
            return False, f"Unknown field type: {field_type}"

    except Exception as e:
        return False, f"Validation error: {str(e)}"

    return True, None


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

    # Most types are already JSON-serializable
    return value


def deserialize_value(value: Any, field_type: FieldType) -> Any:
    """Deserialize a value from JSONB storage.

    Args:
        value: The stored value
        field_type: The field type

    Returns:
        Python-typed value
    """
    if value is None:
        return None

    if field_type == FieldType.DATE:
        if isinstance(value, str):
            return date.fromisoformat(value)
        return value

    elif field_type == FieldType.DATETIME:
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value

    # Most types don't need deserialization
    return value
