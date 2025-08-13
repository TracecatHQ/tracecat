"""Field types and validation for custom entities.

v1: Basic types only with flat JSONB structure (no nested objects).
"""

from datetime import date, datetime
from enum import StrEnum
from types import UnionType
from typing import Any, Protocol
from uuid import UUID

from pydantic_core import PydanticCustomError


class FieldType(StrEnum):
    """Supported field types for custom entities.

    v1: Basic types only, no complex nested structures.
    """

    # Primitive types
    INTEGER = "INTEGER"
    NUMBER = "NUMBER"
    TEXT = "TEXT"
    BOOL = "BOOL"

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

    # Relation types
    RELATION_BELONGS_TO = "RELATION_BELONGS_TO"  # N:1 relationship
    RELATION_HAS_MANY = "RELATION_HAS_MANY"  # 1:N relationship


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
    """Check if a value has flat structure (no nested objects).

    Args:
        value: The value to check

    Returns:
        True if value is flat, False if nested
    """
    if isinstance(value, dict):
        # No nested objects allowed in v1
        return False

    if isinstance(value, list):
        # Arrays allowed but elements must be primitives
        return all(not isinstance(item, dict | list) for item in value)

    return True


def get_python_type(
    field_type: FieldType, is_required: bool = False
) -> type | UnionType | None:
    """Get Python type for a field type.

    Args:
        field_type: The field type
        is_required: Whether field is required (v1: always False)

    Returns:
        Python type for Pydantic model generation, or None for has_many relations
    """
    type_map: dict[FieldType, type | None] = {
        FieldType.INTEGER: int,
        FieldType.NUMBER: float,
        FieldType.TEXT: str,
        FieldType.BOOL: bool,
        FieldType.DATETIME: datetime,
        FieldType.DATE: date,
        FieldType.ARRAY_TEXT: list[str],
        FieldType.ARRAY_INTEGER: list[int],
        FieldType.ARRAY_NUMBER: list[float],
        FieldType.SELECT: str,
        FieldType.MULTI_SELECT: list[str],
        FieldType.RELATION_BELONGS_TO: UUID,  # UUID of related record or None
        FieldType.RELATION_HAS_MANY: None,  # Not stored directly in field_data
    }

    py_type = type_map.get(field_type, Any)

    # Special case: RELATION_HAS_MANY doesn't store data in field_data
    if py_type is None:
        return None

    # In v1, all fields are nullable
    if not is_required:
        return py_type | None

    return py_type


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
                f"Expected integer, got {type(value).__name__}",
            )

    elif field_type == FieldType.NUMBER:
        if not isinstance(value, int | float) or isinstance(value, bool):
            raise PydanticCustomError(
                "invalid_type",
                f"Expected number, got {type(value).__name__}",
            )

    elif field_type == FieldType.TEXT:
        if not isinstance(value, str):
            raise PydanticCustomError(
                "invalid_type",
                f"Expected string, got {type(value).__name__}",
            )
        if len(value) > 65535:  # PostgreSQL text field max
            raise PydanticCustomError(
                "string_too_long",
                f"Text length {len(value)} exceeds maximum 65535",
            )

    elif field_type == FieldType.BOOL:
        if not isinstance(value, bool):
            raise PydanticCustomError(
                "invalid_type",
                f"Expected boolean, got {type(value).__name__}",
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
                f"Expected date or ISO date string, got {type(value).__name__}",
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
                f"Expected datetime or ISO datetime string, got {type(value).__name__}",
            )

    elif field_type == FieldType.SELECT:
        if not isinstance(value, str):
            raise PydanticCustomError(
                "invalid_type",
                f"Expected string for select field, got {type(value).__name__}",
            )
        if enum_options and value not in enum_options:
            raise PydanticCustomError(
                "invalid_enum_value",
                f"Value '{value}' is not in allowed options: {enum_options}",
            )

    elif field_type == FieldType.MULTI_SELECT:
        if not isinstance(value, list):
            raise PydanticCustomError(
                "invalid_type",
                f"Expected list for multi-select field, got {type(value).__name__}",
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
                    f"Values {invalid} are not in allowed options: {enum_options}",
                )

    elif field_type == FieldType.ARRAY_TEXT:
        if not isinstance(value, list):
            raise PydanticCustomError(
                "invalid_type",
                f"Expected list for array field, got {type(value).__name__}",
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
                f"Expected list for array field, got {type(value).__name__}",
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
                f"Expected list for array field, got {type(value).__name__}",
            )
        if not all(
            isinstance(item, int | float) and not isinstance(item, bool)
            for item in value
        ):
            raise PydanticCustomError(
                "invalid_list_item",
                "All array elements must be numbers",
            )

    elif field_type == FieldType.RELATION_BELONGS_TO:
        if value is not None:
            if isinstance(value, str):
                try:
                    return UUID(value)
                except ValueError as e:
                    raise PydanticCustomError(
                        "invalid_uuid",
                        "Invalid UUID format for relation",
                    ) from e
            elif not isinstance(value, UUID):
                raise PydanticCustomError(
                    "invalid_type",
                    f"Expected UUID for belongs_to relation, got {type(value).__name__}",
                )

    elif field_type == FieldType.RELATION_HAS_MANY:
        # Has-many relations are handled separately
        raise PydanticCustomError(
            "invalid_field_type",
            "Has-many relations should be handled through relation endpoints",
        )

    else:
        raise PydanticCustomError(
            "unknown_field_type",
            f"Unknown field type: {field_type}",
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
            f"Field key '{value}' is reserved",
        )

    return value
