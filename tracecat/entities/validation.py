"""Database-dependent validators for custom entities.

This module provides validation logic that requires database queries,
complementing Pydantic's synchronous schema validation.
"""

from typing import Any
from uuid import UUID

from pydantic_core import PydanticCustomError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.db.schemas import (
    EntityData,
    EntityMetadata,
    FieldMetadata,
)
from tracecat.entities.common import validate_value_for_type
from tracecat.entities.query import EntityQueryBuilder
from tracecat.entities.types import FieldType, validate_flat_structure
from tracecat.identifiers import WorkspaceID
from tracecat.types.exceptions import TracecatNotFoundError, TracecatValidationError


class EntityValidators:
    """Database-dependent validators for entity-level operations."""

    def __init__(
        self, session: AsyncSession, workspace_id: str | UUID | WorkspaceID | None
    ):
        """Initialize entity validators.

        Args:
            session: Database session
            workspace_id: Workspace ID for scoping queries
        """
        self.session = session
        self.workspace_id = workspace_id

    async def validate_entity_exists(
        self, entity_id: UUID, raise_on_missing: bool = True
    ) -> EntityMetadata | None:
        """Validate that an entity exists in the workspace.

        Args:
            entity_id: Entity metadata ID to check
            raise_on_missing: Whether to raise exception if not found

        Returns:
            EntityMetadata if found, None if not found and not raising

        Raises:
            TracecatNotFoundError: If entity not found and raise_on_missing=True
        """
        stmt = select(EntityMetadata).where(
            EntityMetadata.id == entity_id,
            EntityMetadata.owner_id == self.workspace_id,
        )
        result = await self.session.exec(stmt)
        entity = result.first()

        if not entity and raise_on_missing:
            raise TracecatNotFoundError(f"Entity with ID {entity_id} not found")
        return entity

    async def validate_entity_name_unique(
        self, name: str, exclude_id: UUID | None = None
    ) -> None:
        """Check if entity name is unique in workspace.

        Args:
            name: Entity name to check
            exclude_id: Entity ID to exclude (for updates)

        Raises:
            PydanticCustomError: If name already exists
        """
        stmt = select(EntityMetadata).where(
            EntityMetadata.name == name,
            EntityMetadata.owner_id == self.workspace_id,
        )
        if exclude_id:
            stmt = stmt.where(EntityMetadata.id != exclude_id)

        result = await self.session.exec(stmt)
        if result.first():
            raise PydanticCustomError(
                "unique_violation",
                "Entity name '{name}' already exists",
                {"name": name},
            )

    async def validate_entity_active(self, entity_id: UUID) -> EntityMetadata:
        """Validate that an entity is active.

        Args:
            entity_id: Entity metadata ID to check

        Returns:
            EntityMetadata if active

        Raises:
            TracecatValidationError: If entity is not active
            TracecatNotFoundError: If entity not found
        """
        entity = await self.validate_entity_exists(entity_id)
        if not entity:
            raise TracecatNotFoundError(f"Entity with ID {entity_id} not found")
        if not entity.is_active:
            raise TracecatValidationError(f"Entity {entity.name} is not active")
        return entity


class FieldValidators:
    """Database-dependent validators for field-level operations."""

    def __init__(
        self, session: AsyncSession, workspace_id: str | UUID | WorkspaceID | None
    ):
        """Initialize field validators.

        Args:
            session: Database session
            workspace_id: Workspace ID for scoping queries
        """
        self.session = session
        self.workspace_id = workspace_id

    async def validate_field_exists(
        self, field_id: UUID, raise_on_missing: bool = True
    ) -> FieldMetadata | None:
        """Validate that a field exists.

        Args:
            field_id: Field metadata ID to check
            raise_on_missing: Whether to raise exception if not found

        Returns:
            FieldMetadata if found, None if not found and not raising

        Raises:
            TracecatNotFoundError: If field not found and raise_on_missing=True
        """
        # Join with EntityMetadata to check workspace ownership
        stmt = (
            select(FieldMetadata)
            .join(EntityMetadata, FieldMetadata.entity_metadata_id == EntityMetadata.id)  # type: ignore[arg-type]
            .where(
                FieldMetadata.id == field_id,
                EntityMetadata.owner_id == self.workspace_id,
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
        """Check if field key is unique within entity.

        Args:
            entity_id: Entity metadata ID
            field_key: Field key to check
            exclude_id: Field ID to exclude (for updates)

        Raises:
            PydanticCustomError: If field key already exists
        """
        # Join with EntityMetadata to check workspace ownership
        stmt = (
            select(FieldMetadata)
            .join(EntityMetadata, FieldMetadata.entity_metadata_id == EntityMetadata.id)  # type: ignore[arg-type]
            .where(
                FieldMetadata.entity_metadata_id == entity_id,
                FieldMetadata.field_key == field_key,
                EntityMetadata.owner_id == self.workspace_id,
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
        """Validate that a field is active.

        Args:
            field_id: Field metadata ID to check

        Returns:
            FieldMetadata if active

        Raises:
            TracecatValidationError: If field is not active
            TracecatNotFoundError: If field not found
        """
        field = await self.validate_field_exists(field_id)
        if not field:
            raise TracecatNotFoundError(f"Field with ID {field_id} not found")
        if not field.is_active:
            raise TracecatValidationError(f"Field {field.field_key} is not active")
        return field


class RecordValidators:
    """Database-dependent validators for record-level operations."""

    def __init__(
        self,
        session: AsyncSession,
        workspace_id: str | UUID | WorkspaceID | None,
        query_builder: EntityQueryBuilder | None = None,
    ):
        """Initialize record validators.

        Args:
            session: Database session
            workspace_id: Workspace ID for scoping queries
            query_builder: Optional query builder for complex queries
        """
        self.session = session
        self.workspace_id = workspace_id
        self.query_builder = query_builder or EntityQueryBuilder(session)

    async def validate_record_exists(
        self,
        record_id: UUID,
        entity_id: UUID | None = None,
        raise_on_missing: bool = True,
    ) -> EntityData | None:
        """Validate that a record exists.

        Args:
            record_id: Record ID to check
            entity_id: Optional entity ID to verify record belongs to entity
            raise_on_missing: Whether to raise exception if not found

        Returns:
            EntityData if found, None if not found and not raising

        Raises:
            TracecatNotFoundError: If record not found and raise_on_missing=True
        """
        stmt = select(EntityData).where(
            EntityData.id == record_id,
            EntityData.owner_id == self.workspace_id,
        )
        if entity_id:
            stmt = stmt.where(EntityData.entity_metadata_id == entity_id)

        result = await self.session.exec(stmt)
        record = result.first()

        if not record and raise_on_missing:
            raise TracecatNotFoundError(f"Record with ID {record_id} not found")
        return record

    async def validate_record_data(
        self,
        data: dict[str, Any],
        fields: list[FieldMetadata],
        exclude_record_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Validate record data against field definitions.

        Args:
            data: Record data to validate
            fields: Field definitions
            exclude_record_id: Record ID to exclude for unique checks

        Returns:
            Validated data dict

        Raises:
            TracecatValidationError: If validation fails
        """
        errors = []
        validated = {}

        active_fields = {f.field_key: f for f in fields if f.is_active}

        # Check for flat structure (no nested objects) - only for active fields
        for key, value in data.items():
            if key in active_fields and not validate_flat_structure(value):
                errors.append(f"Field '{key}': Nested objects not allowed")

        if errors:
            raise TracecatValidationError("; ".join(errors))

        for key, value in data.items():
            if key not in active_fields:
                # Silently skip inactive/unknown fields
                continue

            field = active_fields[key]

            if value is not None:
                is_valid, error = validate_value_for_type(
                    value, FieldType(field.field_type), field.enum_options
                )
                if not is_valid:
                    errors.append(f"Field '{key}': {error}")
                    continue

            validated[key] = value

        if errors:
            raise TracecatValidationError("; ".join(errors))

        return validated


class RelationValidators:
    """Database-dependent validators for relation operations."""

    def __init__(
        self, session: AsyncSession, workspace_id: str | UUID | WorkspaceID | None
    ):
        """Initialize relation validators.

        Args:
            session: Database session
            workspace_id: Workspace ID for scoping queries
        """
        self.session = session
        self.workspace_id = workspace_id
        self.entity_validators = EntityValidators(session, workspace_id)
        self.record_validators = RecordValidators(session, workspace_id)

    async def validate_target_entity(self, target_entity_name: str) -> EntityMetadata:
        """Validate that target entity exists and is active.

        Args:
            target_entity_name: Name of target entity

        Returns:
            EntityMetadata of target entity

        Raises:
            TracecatNotFoundError: If entity not found
            TracecatValidationError: If entity not active
        """
        stmt = select(EntityMetadata).where(
            EntityMetadata.name == target_entity_name,
            EntityMetadata.owner_id == self.workspace_id,
        )
        result = await self.session.exec(stmt)
        entity = result.first()

        if not entity:
            raise TracecatNotFoundError(
                f"Target entity '{target_entity_name}' not found"
            )

        if not entity.is_active:
            raise TracecatValidationError(
                f"Target entity '{target_entity_name}' is not active"
            )

        return entity

    async def validate_target_record(
        self, record_id: UUID, target_entity_id: UUID
    ) -> EntityData:
        """Validate that target record exists and belongs to target entity.

        Args:
            record_id: Target record ID
            target_entity_id: Expected entity ID

        Returns:
            EntityData of target record

        Raises:
            TracecatNotFoundError: If record not found
        """
        record = await self.record_validators.validate_record_exists(
            record_id, target_entity_id
        )
        if not record:
            raise TracecatNotFoundError(
                f"Target record {record_id} not found in entity"
            )
        return record
