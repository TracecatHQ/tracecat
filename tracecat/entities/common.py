"""Common utilities and validators for custom entities."""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from tracecat.entities.enums import RelationType
from tracecat.entities.models import RelationSettings
from tracecat.entities.types import FieldType, validate_field_value_type


def validate_value_for_type(
    value: Any, field_type: FieldType, enum_options: list[str] | None = None
) -> tuple[bool, str | None]:
    """Validate a value against a field type.

    This is a compatibility wrapper around the new Pydantic validator.

    Args:
        value: The value to validate
        field_type: The expected field type
        enum_options: Options for SELECT/MULTI_SELECT fields

    Returns:
        Tuple of (is_valid, error_message)
    """
    from pydantic_core import PydanticCustomError

    try:
        validate_field_value_type(value, field_type, enum_options)
        return True, None
    except PydanticCustomError as e:
        # Extract error message from Pydantic error
        return False, e.message()


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
            RelationType.BELONGS_TO
            if field_type == FieldType.RELATION_BELONGS_TO
            else RelationType.HAS_MANY
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
