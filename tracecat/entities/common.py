"""Common utilities and validators for custom entities."""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from tracecat.entities.models import RelationSettings
from tracecat.entities.types import FieldType


def validate_value_for_type(
    value: Any, field_type: FieldType, enum_options: list[str] | None = None
) -> tuple[bool, str | None]:
    """Validate a value against a field type.

    Args:
        value: The value to validate
        field_type: The expected field type
        enum_options: Options for SELECT/MULTI_SELECT fields

    Returns:
        Tuple of (is_valid, error_message)
    """

    # Handle None values (all fields nullable in v1)
    if value is None:
        return True, None

    try:
        if field_type == FieldType.INTEGER:
            if not isinstance(value, int) or isinstance(value, bool):
                return False, f"Expected integer, got {type(value).__name__}"

            # No configurable min/max for integers in v1

        elif field_type == FieldType.NUMBER:
            if not isinstance(value, int | float) or isinstance(value, bool):
                return False, f"Expected number, got {type(value).__name__}"

            # No configurable min/max for numbers in v1

        elif field_type == FieldType.TEXT:
            if not isinstance(value, str):
                return False, f"Expected string, got {type(value).__name__}"

            # Hard-coded max length for text fields
            if len(value) > 65535:  # PostgreSQL text field max
                return (
                    False,
                    f"Text length {len(value)} exceeds maximum 65535",
                )

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
            if enum_options and value not in enum_options:
                return (
                    False,
                    f"Value '{value}' is not in allowed options: {enum_options}",
                )

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
            if enum_options:
                invalid = [v for v in value if v not in enum_options]
                if invalid:
                    return (
                        False,
                        f"Values {invalid} are not in allowed options: {enum_options}",
                    )

        elif field_type == FieldType.ARRAY_TEXT:
            if not isinstance(value, list):
                return (
                    False,
                    f"Expected list for array field, got {type(value).__name__}",
                )

            if not all(isinstance(item, str) for item in value):
                return False, "All array elements must be strings"

            # No array length constraints in v1

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

            # No array length constraints in v1

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

            # No array length constraints in v1

        elif field_type == FieldType.RELATION_BELONGS_TO:
            # Belongs-to expects a UUID or None
            if value is not None:
                if isinstance(value, str):
                    try:
                        UUID(value)
                    except ValueError:
                        return False, "Invalid UUID format for relation"
                elif not isinstance(value, UUID):
                    return (
                        False,
                        f"Expected UUID for belongs_to relation, got {type(value).__name__}",
                    )

        elif field_type == FieldType.RELATION_HAS_MANY:
            # Has-many expects a structured update payload, not stored directly
            if not isinstance(value, dict):
                return False, "Has-many relations require structured update payload"

            # Validate it's a proper HasManyRelationUpdate structure
            if "operation" not in value or "target_ids" not in value:
                return (
                    False,
                    "Has-many update must include 'operation' and 'target_ids'",
                )

            if value["operation"] not in ["add", "remove", "replace"]:
                return False, f"Invalid operation: {value['operation']}"

            if not isinstance(value["target_ids"], list):
                return False, "target_ids must be a list"

            # Validate all IDs are valid UUIDs
            for tid in value["target_ids"]:
                if isinstance(tid, str):
                    try:
                        UUID(tid)
                    except ValueError:
                        return False, f"Invalid UUID in target_ids: {tid}"
                elif not isinstance(tid, UUID):
                    return False, f"Invalid type in target_ids: {type(tid).__name__}"

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

    elif field_type == FieldType.RELATION_BELONGS_TO:
        # Store UUID as string for JSONB
        if isinstance(value, UUID):
            return str(value)
        return value

    elif field_type == FieldType.RELATION_HAS_MANY:
        # Has-many relations are not stored in field_data
        return None

    # Most types are already JSON-serializable
    return value


def validate_relation_settings(
    field_type: FieldType, relation_settings: RelationSettings | None
) -> tuple[bool, str | None]:
    """Validate relation settings match field type.

    Args:
        field_type: The field type
        relation_settings: The relation settings to validate

    Returns:
        Tuple of (is_valid, error_message)
    """
    is_relation = field_type in (
        FieldType.RELATION_BELONGS_TO,
        FieldType.RELATION_HAS_MANY,
    )

    if is_relation and not relation_settings:
        return False, f"Relation field type {field_type} requires relation_settings"

    if not is_relation and relation_settings:
        return (
            False,
            f"Non-relation field type {field_type} cannot have relation_settings",
        )

    if relation_settings:
        # Validate relation_type matches field_type
        expected_type = (
            "belongs_to" if field_type == FieldType.RELATION_BELONGS_TO else "has_many"
        )
        if relation_settings.relation_type != expected_type:
            return (
                False,
                f"Relation type {relation_settings.relation_type} doesn't match field type {field_type}",
            )

    return True, None


def format_belongs_to_cache(
    record_id: UUID, display_value: str | None = None
) -> dict[str, Any]:
    """Format belongs-to relation for JSONB cache.

    Args:
        record_id: The related record's UUID
        display_value: Optional display value for the relation

    Returns:
        Formatted cache dict
    """
    return {
        "id": str(record_id),
        "display": display_value or str(record_id),
    }


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

    elif field_type == FieldType.RELATION_BELONGS_TO:
        # Convert string UUID to UUID object
        if isinstance(value, str):
            return UUID(value)
        return value

    elif field_type == FieldType.RELATION_HAS_MANY:
        # Has-many relations are not stored in field_data
        return None

    # Most types don't need deserialization
    return value
