"""Field types and validation for custom entities."""

from datetime import date, datetime
from enum import StrEnum
from types import UnionType
from typing import Any, Protocol

from pydantic_core import PydanticCustomError


class FieldType(StrEnum):
    """Supported field types for custom entities."""

    # Primitive types
    INTEGER = "INTEGER"
    NUMBER = "NUMBER"
    TEXT = "TEXT"
    BOOL = "BOOL"
    JSON = "JSON"  # Structured data (dict/list with depth limits)

    # Date/Time (stored as ISO strings)
    DATETIME = "DATETIME"
    DATE = "DATE"

    # Simple arrays (no nested objects)
    ARRAY_TEXT = "ARRAY_TEXT"
    ARRAY_INTEGER = "ARRAY_INTEGER"
    ARRAY_NUMBER = "ARRAY_NUMBER"

    # Single select (stored as string)
    SELECT = "SELECT"

    # Multi-select (stored as array of strings)
    MULTI_SELECT = "MULTI_SELECT"


class FieldValidator(Protocol):
    """Protocol for field type validators."""

    def validate_flat_structure(self, value: Any) -> bool:
        """Ensure value is not a nested object.

        Args:
            value: The value to validate

        Returns:
            True if value is flat (not nested), False otherwise
        """
        ...

    def validate_type(
        self, value: Any, field_type: FieldType, settings: dict[str, Any]
    ) -> tuple[bool, str | None]:
        """Validate value against field type and settings.

        Args:
            value: The value to validate
            field_type: The expected field type
            settings: Field-specific settings (e.g., min/max, options)

        Returns:
            Tuple of (is_valid, error_message)
        """
        ...


def validate_flat_structure(value: Any) -> bool:
    """Check if a value has acceptable structure.

    Nested objects are now allowed, but we still prevent:
    - Nested arrays (arrays within arrays)
    - Excessive nesting depth (more than 3 levels)

    Args:
        value: The value to check

    Returns:
        True if value has acceptable structure, False otherwise
    """

    def check_depth(obj: Any, current_depth: int = 0, max_depth: int = 3) -> bool:
        """Check nesting depth doesn't exceed max_depth."""
        if current_depth > max_depth:
            return False

        if isinstance(obj, dict):
            return all(
                check_depth(v, current_depth + 1, max_depth) for v in obj.values()
            )
        elif isinstance(obj, list):
            # Arrays are allowed, but elements cannot be arrays (no nested arrays)
            for item in obj:
                if isinstance(item, list):
                    return False  # No nested arrays
                if isinstance(item, dict):
                    if not check_depth(item, current_depth + 1, max_depth):
                        return False
            return True

        return True  # Primitives are always OK

    return check_depth(value)


def get_python_type(field_type: FieldType) -> type | UnionType | None:
    """Get Python type for a field type.

    Args:
        field_type: The field type

    Returns:
        Python type for Pydantic model generation (always Optional in v1), or None for one_to_many relations
    """
    type_map: dict[FieldType, type | UnionType | None] = {
        FieldType.INTEGER: int,
        FieldType.NUMBER: float,
        FieldType.TEXT: str,
        FieldType.BOOL: bool,
        FieldType.JSON: dict | list,  # Can be either dict or list
        FieldType.DATETIME: datetime,
        FieldType.DATE: date,
        FieldType.ARRAY_TEXT: list[str],
        FieldType.ARRAY_INTEGER: list[int],
        FieldType.ARRAY_NUMBER: list[float],
        FieldType.SELECT: str,
        FieldType.MULTI_SELECT: list[str],
    }

    py_type = type_map.get(field_type, object)
    if py_type is None:
        return None
    return py_type | type(None)


def validate_field_value_type(
    value: Any, field_type: FieldType, enum_options: list[str] | None = None
) -> Any:
    """Pydantic-compatible validator for field values.

    Can be used as a field_validator in Pydantic models.

    Args:
        value: The value to validate
        field_type: The expected field type
        enum_options: Options for SELECT/MULTI_SELECT fields

    Returns:
        The validated value

    Raises:
        PydanticCustomError: If validation fails
    """
    if value is None:
        return value

    if field_type == FieldType.INTEGER:
        if not isinstance(value, int) or isinstance(value, bool):
            raise PydanticCustomError(
                "invalid_type",
                "Expected integer, got {type_name}",
                {"type_name": type(value).__name__},
            )

    elif field_type == FieldType.NUMBER:
        if not isinstance(value, int | float) or isinstance(value, bool):
            raise PydanticCustomError(
                "invalid_type",
                "Expected number, got {type_name}",
                {"type_name": type(value).__name__},
            )

    elif field_type == FieldType.TEXT:
        if not isinstance(value, str):
            raise PydanticCustomError(
                "invalid_type",
                "Expected string, got {type_name}",
                {"type_name": type(value).__name__},
            )
        if len(value) > 65535:  # PostgreSQL text field max
            raise PydanticCustomError(
                "string_too_long",
                "Text length {length} exceeds maximum 65535",
                {"length": len(value)},
            )

    elif field_type == FieldType.BOOL:
        if not isinstance(value, bool):
            raise PydanticCustomError(
                "invalid_type",
                "Expected boolean, got {type_name}",
                {"type_name": type(value).__name__},
            )

    elif field_type == FieldType.JSON:
        # JSON field accepts dict or list as top-level structure
        if not isinstance(value, dict | list):
            raise PydanticCustomError(
                "invalid_type",
                "Expected dict or list for JSON field, got {type_name}",
                {"type_name": type(value).__name__},
            )
        # Validate structure depth and no nested arrays
        if not validate_flat_structure(value):
            raise PydanticCustomError(
                "invalid_json_structure",
                "JSON field cannot contain nested arrays or exceed 3 levels of nesting",
            )

    elif field_type == FieldType.DATE:
        if isinstance(value, str):
            try:
                return date.fromisoformat(value)
            except ValueError as e:
                raise PydanticCustomError(
                    "invalid_date",
                    "Invalid date format, expected YYYY-MM-DD",
                ) from e
        elif not isinstance(value, date):
            raise PydanticCustomError(
                "invalid_type",
                "Expected date or ISO date string, got {type_name}",
                {"type_name": type(value).__name__},
            )

    elif field_type == FieldType.DATETIME:
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as e:
                raise PydanticCustomError(
                    "invalid_datetime",
                    "Invalid datetime format, expected ISO 8601",
                ) from e
        elif not isinstance(value, datetime):
            raise PydanticCustomError(
                "invalid_type",
                "Expected datetime or ISO datetime string, got {type_name}",
                {"type_name": type(value).__name__},
            )

    elif field_type == FieldType.SELECT:
        if not isinstance(value, str):
            raise PydanticCustomError(
                "invalid_type",
                "Expected string for select field, got {type_name}",
                {"type_name": type(value).__name__},
            )
        if enum_options and value not in enum_options:
            raise PydanticCustomError(
                "invalid_enum_value",
                "Value '{value}' is not in allowed options: {options}",
                {"value": value, "options": enum_options},
            )

    elif field_type == FieldType.MULTI_SELECT:
        if not isinstance(value, list):
            raise PydanticCustomError(
                "invalid_type",
                "Expected list for multi-select field, got {type_name}",
                {"type_name": type(value).__name__},
            )
        # Guard nested arrays for clearer errors
        if any(isinstance(item, list) for item in value):
            raise PydanticCustomError(
                "nested_array_not_allowed",
                "Nested arrays are not allowed",
            )
        if not all(isinstance(item, str) for item in value):
            raise PydanticCustomError(
                "invalid_list_item",
                "All multi-select values must be strings",
            )
        if enum_options:
            invalid = [v for v in value if v not in enum_options]
            if invalid:
                raise PydanticCustomError(
                    "invalid_enum_values",
                    "Values {invalid} are not in allowed options: {options}",
                    {"invalid": invalid, "options": enum_options},
                )

    elif field_type == FieldType.ARRAY_TEXT:
        if not isinstance(value, list):
            raise PydanticCustomError(
                "invalid_type",
                "Expected list for array field, got {type_name}",
                {"type_name": type(value).__name__},
            )
        if any(isinstance(item, list) for item in value):
            raise PydanticCustomError(
                "nested_array_not_allowed",
                "Nested arrays are not allowed",
            )
        if not all(isinstance(item, str) for item in value):
            raise PydanticCustomError(
                "invalid_list_item",
                "All array elements must be strings",
            )

    elif field_type == FieldType.ARRAY_INTEGER:
        if not isinstance(value, list):
            raise PydanticCustomError(
                "invalid_type",
                "Expected list for array field, got {type_name}",
                {"type_name": type(value).__name__},
            )
        if any(isinstance(item, list) for item in value):
            raise PydanticCustomError(
                "nested_array_not_allowed",
                "Nested arrays are not allowed",
            )
        if not all(
            isinstance(item, int) and not isinstance(item, bool) for item in value
        ):
            raise PydanticCustomError(
                "invalid_list_item",
                "All array elements must be integers",
            )

    elif field_type == FieldType.ARRAY_NUMBER:
        if not isinstance(value, list):
            raise PydanticCustomError(
                "invalid_type",
                "Expected list for array field, got {type_name}",
                {"type_name": type(value).__name__},
            )
        if any(isinstance(item, list) for item in value):
            raise PydanticCustomError(
                "nested_array_not_allowed",
                "Nested arrays are not allowed",
            )
        if not all(
            isinstance(item, int | float) and not isinstance(item, bool)
            for item in value
        ):
            raise PydanticCustomError(
                "invalid_list_item",
                "All array elements must be numbers",
            )

    else:
        raise PydanticCustomError(
            "unknown_field_type",
            "Unknown field type: {field_type}",
            {"field_type": field_type},
        )

    return value


def validate_field_key_format(value: str) -> str:
    """Validate field key format for Pydantic models.

    Field keys must be:
    - Alphanumeric with underscores only
    - Start with a letter
    - Not exceed 100 characters
    - Lowercase
    - Not use reserved keywords

    Args:
        value: The field key to validate

    Returns:
        The validated field key

    Raises:
        PydanticCustomError: If validation fails
    """
    if not value:
        raise PydanticCustomError("empty_field_key", "Field key cannot be empty")

    if len(value) > 100:
        raise PydanticCustomError(
            "field_key_too_long",
            "Field key cannot exceed 100 characters",
        )

    if not value[0].isalpha():
        raise PydanticCustomError(
            "invalid_field_key_start",
            "Field key must start with a letter",
        )

    if not value.replace("_", "").isalnum():
        raise PydanticCustomError(
            "invalid_field_key_chars",
            "Field key must be alphanumeric with underscores only",
        )

    if value != value.lower():
        raise PydanticCustomError(
            "field_key_not_lowercase",
            "Field key must be lowercase",
        )

    # Reserved keywords
    reserved = {"id", "created_at", "updated_at", "owner_id", "field_data"}
    if value in reserved:
        raise PydanticCustomError(
            "reserved_field_key",
            "Field key '{value}' is reserved",
            {"value": value},
        )

    return value
