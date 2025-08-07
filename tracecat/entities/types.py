"""Field types and validation for custom entities.

v1: Basic types only with flat JSONB structure (no nested objects).
"""

from datetime import date, datetime
from enum import StrEnum
from types import UnionType
from typing import Any, Protocol


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
) -> type | UnionType:
    """Get Python type for a field type.

    Args:
        field_type: The field type
        is_required: Whether field is required (v1: always False)

    Returns:
        Python type for Pydantic model generation
    """
    type_map = {
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
    }

    py_type = type_map.get(field_type, Any)

    # In v1, all fields are nullable
    if not is_required:
        return py_type | None

    return py_type
