"""Common utilities and validators for custom entities."""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from tracecat.entities.enums import RelationType
from tracecat.entities.models import RelationSettings
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

    elif field_type in (FieldType.RELATION_ONE_TO_ONE, FieldType.RELATION_MANY_TO_ONE):
        # Store UUID as string for JSONB
        if isinstance(value, UUID):
            return str(value)
        return value

    elif field_type in (
        FieldType.RELATION_ONE_TO_MANY,
        FieldType.RELATION_MANY_TO_MANY,
    ):
        # One-to-many relations are not stored in field_data
        return None

    elif field_type == FieldType.JSON:
        # JSON values are already serializable
        return value

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
        FieldType.RELATION_ONE_TO_ONE,
        FieldType.RELATION_ONE_TO_MANY,
        FieldType.RELATION_MANY_TO_ONE,
        FieldType.RELATION_MANY_TO_MANY,
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
        if field_type in (
            FieldType.RELATION_ONE_TO_ONE,
            FieldType.RELATION_MANY_TO_ONE,
        ):
            expected_type = (
                RelationType.ONE_TO_ONE
                if field_type == FieldType.RELATION_ONE_TO_ONE
                else RelationType.MANY_TO_ONE
            )
        else:
            expected_type = (
                RelationType.ONE_TO_MANY
                if field_type == FieldType.RELATION_ONE_TO_MANY
                else RelationType.MANY_TO_MANY
            )
        if relation_settings.relation_type != expected_type:
            return (
                False,
                f"Relation type {relation_settings.relation_type} doesn't match field type {field_type}",
            )

    return True, None


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
