"""Field-level validators and pure helpers.

Includes:
- validate_enum_options: uniqueness and non-empty checks for enum options
- validate_default_value_type: shared default-value validator using field types
- FieldValidators: DB checks for existence, uniqueness, and active state
"""

from typing import Any, cast
from uuid import UUID

from pydantic_core import PydanticCustomError
from sqlalchemy.sql import ColumnElement
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.db.schemas import Entity, FieldMetadata
from tracecat.entities.types import (
    FieldType,
    validate_field_value_type,
    validate_flat_structure,
)
from tracecat.identifiers import WorkspaceID
from tracecat.types.exceptions import TracecatNotFoundError, TracecatValidationError


def validate_enum_options(options: list[str] | None) -> list[str] | None:
    """Validate enum options are unique and non-empty.

    Returns the original list (or None) or raises PydanticCustomError.
    """
    if options is None:
        return None

    if len(set(options)) != len(options):
        raise PydanticCustomError(
            "duplicate_enum_options",
            "Enum options must be unique",
        )
    for opt in options:
        if not opt or not opt.strip():
            raise PydanticCustomError(
                "empty_enum_option",
                "Enum options cannot be empty strings",
            )
    return options


def validate_default_value_type(
    value: Any, field_type: FieldType, enum_options: list[str] | None = None
) -> Any:
    """Validate a default value matches the field type and structure.

    - Rejects default values for unsupported types (arrays, date/datetime)
    - Enforces no nested arrays and max 3-level depth for JSON-like defaults
    - Delegates type checks to validate_field_value_type
    """
    if value is None:
        return None

    unsupported_types = {
        FieldType.ARRAY_TEXT,
        FieldType.ARRAY_INTEGER,
        FieldType.ARRAY_NUMBER,
        FieldType.DATE,
        FieldType.DATETIME,
    }
    if field_type in unsupported_types:
        raise PydanticCustomError(
            "default_not_supported",
            "Field type '{field_type}' does not support default values",
            {"field_type": field_type.value},
        )

    if not validate_flat_structure(value):
        raise PydanticCustomError(
            "invalid_structure",
            "Default values cannot contain nested arrays or exceed 3 levels of nesting",
        )

    return validate_field_value_type(value, field_type, enum_options)


class FieldValidators:
    """Database-dependent validators for field-level operations."""

    def __init__(
        self, session: AsyncSession, workspace_id: str | UUID | WorkspaceID | None
    ):
        self.session = session
        self.workspace_id = workspace_id

    async def validate_field_exists(
        self, field_id: UUID, raise_on_missing: bool = True
    ) -> FieldMetadata | None:
        """Validate that a field exists and belongs to this workspace."""
        stmt = (
            select(FieldMetadata)
            .join(
                Entity,
                cast(ColumnElement[Any], FieldMetadata.entity_id)
                == cast(ColumnElement[Any], Entity.id),
            )
            .where(
                FieldMetadata.id == field_id,
                Entity.owner_id == self.workspace_id,
            )
        )
        result = await self.session.exec(stmt)
        field = result.first()
        if not field and raise_on_missing:
            raise TracecatNotFoundError(f"Field with ID {field_id} not found")
        return field

    async def validate_field_key_unique(
        self,
        entity_id: UUID,
        field_key: str,
        exclude_id: UUID | None = None,
    ) -> None:
        """Pre-check field key uniqueness within an entity for better UX."""
        stmt = (
            select(FieldMetadata)
            .join(
                Entity,
                cast(ColumnElement[Any], FieldMetadata.entity_id)
                == cast(ColumnElement[Any], Entity.id),
            )
            .where(
                FieldMetadata.entity_id == entity_id,
                FieldMetadata.field_key == field_key,
                Entity.owner_id == self.workspace_id,
            )
        )
        if exclude_id:
            stmt = stmt.where(FieldMetadata.id != exclude_id)

        result = await self.session.exec(stmt)
        if result.first():
            raise PydanticCustomError(
                "unique_violation",
                "Field key '{field_key}' already exists in entity",
                {"field_key": field_key},
            )

    async def validate_field_active(self, field_id: UUID) -> FieldMetadata:
        """Ensure a field exists and is active; return it."""
        field = cast(FieldMetadata, await self.validate_field_exists(field_id))
        if not field.is_active:
            raise TracecatValidationError(f"Field {field.field_key} is not active")
        return field
