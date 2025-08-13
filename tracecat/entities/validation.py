"""Database-dependent validators for custom entities.

This module provides validation logic that requires database queries,
complementing Pydantic's synchronous schema validation.
"""

from typing import Any
from uuid import UUID

from pydantic_core import PydanticCustomError
from sqlalchemy import text
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.db.schemas import (
    EntityData,
    EntityMetadata,
    EntityRelationLink,
    FieldMetadata,
)
from tracecat.entities.common import validate_relation_settings, validate_value_for_type
from tracecat.entities.models import RelationSettings
from tracecat.entities.query import EntityQueryBuilder
from tracecat.entities.types import FieldType, validate_flat_structure
from tracecat.types.exceptions import TracecatNotFoundError, TracecatValidationError


class EntityValidators:
    """Database-dependent validators for entity-level operations."""

    def __init__(self, session: AsyncSession, workspace_id: str | UUID):
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

    def __init__(self, session: AsyncSession, workspace_id: str | UUID):
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

    def validate_enum_options_for_type(
        self, field_type: FieldType, enum_options: list[str] | None
    ) -> None:
        """Validate enum options are provided for SELECT/MULTI_SELECT types.

        Args:
            field_type: Field type
            enum_options: Enum options list

        Raises:
            PydanticCustomError: If validation fails
        """
        if field_type in (FieldType.SELECT, FieldType.MULTI_SELECT):
            if not enum_options:
                raise PydanticCustomError(
                    "missing_enum_options",
                    "Field type '{field_type}' requires enum_options",
                    {"field_type": field_type.value},
                )
        elif enum_options:
            raise PydanticCustomError(
                "invalid_enum_options",
                "Field type '{field_type}' cannot have enum_options",
                {"field_type": field_type.value},
            )

    def validate_field_type_supports_unique(self, field_type: FieldType) -> None:
        """Validate that field type supports unique constraint.

        Args:
            field_type: Field type to check

        Raises:
            PydanticCustomError: If type doesn't support unique
        """
        supported_types = {FieldType.TEXT, FieldType.NUMBER}
        if field_type not in supported_types:
            raise PydanticCustomError(
                "constraint_not_supported",
                "Field type '{field_type}' does not support unique constraint",
                {"field_type": field_type.value},
            )

    def validate_field_type_supports_default(self, field_type: FieldType) -> None:
        """Validate that field type supports default values.

        Args:
            field_type: Field type to check

        Raises:
            PydanticCustomError: If type doesn't support defaults
        """
        unsupported_types = {
            FieldType.RELATION_BELONGS_TO,
            FieldType.RELATION_HAS_MANY,
        }
        if field_type in unsupported_types:
            raise PydanticCustomError(
                "default_not_supported",
                "Field type '{field_type}' does not support default values",
                {"field_type": field_type.value},
            )


class RecordValidators:
    """Database-dependent validators for record-level operations."""

    def __init__(
        self,
        session: AsyncSession,
        workspace_id: str,
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

    async def check_unique_violation(
        self,
        entity_id: UUID,
        field_key: str,
        value: Any,
        exclude_record_id: UUID | None = None,
    ) -> bool:
        """Check if a value would violate unique constraint.

        Args:
            entity_id: Entity metadata ID
            field_key: Field key to check
            value: Value to check for uniqueness
            exclude_record_id: Record ID to exclude (for updates)

        Returns:
            True if duplicate exists, False otherwise
        """
        stmt = select(EntityData).where(
            EntityData.entity_metadata_id == entity_id,
            EntityData.owner_id == self.workspace_id,
        )

        expr = await self.query_builder.eq(entity_id, field_key, value)
        stmt = stmt.where(expr)

        if exclude_record_id:
            stmt = stmt.where(EntityData.id != exclude_record_id)

        stmt = stmt.limit(1)
        result = await self.session.exec(stmt)
        return result.first() is not None

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

        validate_flat_structure(data)

        active_fields = {f.field_key: f for f in fields if f.is_active}

        for key, value in data.items():
            if key not in active_fields:
                errors.append(f"Field '{key}' is not active or doesn't exist")
                continue

            field = active_fields[key]

            if value is not None:
                is_valid, error = validate_value_for_type(
                    value, FieldType(field.field_type), field.enum_options
                )
                if not is_valid:
                    errors.append(f"Field '{key}': {error}")
                    continue

                if field.is_unique:
                    has_duplicate = await self.check_unique_violation(
                        field.entity_metadata_id, key, value, exclude_record_id
                    )
                    if has_duplicate:
                        errors.append(f"Field '{key}': Value '{value}' already exists")
                        continue

            validated[key] = value

        if errors:
            raise TracecatValidationError("; ".join(errors))

        return validated

    def validate_required_fields(
        self, data: dict[str, Any], fields: list[FieldMetadata]
    ) -> None:
        """Validate that all required fields have values.

        Args:
            data: Record data
            fields: Field definitions

        Raises:
            TracecatValidationError: If required fields are missing
        """
        errors = []
        for field in fields:
            if field.is_required and field.is_active:
                value = data.get(field.field_key)
                if value is None:
                    errors.append(f"Required field '{field.field_key}' is missing")

        if errors:
            raise TracecatValidationError("; ".join(errors))


class RelationValidators:
    """Database-dependent validators for relation operations."""

    def __init__(self, session: AsyncSession, workspace_id: str | UUID):
        """Initialize relation validators.

        Args:
            session: Database session
            workspace_id: Workspace ID for scoping queries
        """
        self.session = session
        self.workspace_id = workspace_id
        self.entity_validators = EntityValidators(session, workspace_id)
        self.record_validators = RecordValidators(session, str(workspace_id))

    def validate_relation_settings(
        self, field_type: FieldType, relation_settings: RelationSettings | None
    ) -> None:
        """Validate relation settings match field type.

        Args:
            field_type: Field type
            relation_settings: Relation settings to validate

        Raises:
            PydanticCustomError: If validation fails
        """
        is_valid, error = validate_relation_settings(
            FieldType(field_type), relation_settings
        )
        if not is_valid:
            details = error or "Invalid relation settings"
            raise PydanticCustomError(
                "invalid_relation_settings",
                "Invalid relation settings: {details}",
                {"details": details},
            )

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

    async def validate_relation_required_constraint(
        self, field: FieldMetadata, record_id: UUID
    ) -> None:
        """Validate that a required relation has a value.

        Args:
            field: Field metadata with relation settings
            record_id: Record ID to check

        Raises:
            TracecatValidationError: If required relation is missing
        """
        if not field.is_required or not field.relation_kind:
            return

        if field.relation_kind == "belongs_to":
            record = await self.record_validators.validate_record_exists(record_id)
            if record:
                value = record.field_data.get(field.field_key)
                if value is None:
                    raise TracecatValidationError(
                        f"Required relation field '{field.field_key}' is missing"
                    )

    async def validate_unique_relation(
        self,
        field: FieldMetadata,
        target_record_id: UUID,
        exclude_record_id: UUID | None = None,
    ) -> None:
        """Validate unique constraint on has-many relation.

        Args:
            field: Field metadata with relation settings
            target_record_id: Target record ID
            exclude_record_id: Record to exclude from check

        Raises:
            TracecatValidationError: If relation would violate unique constraint
        """
        if not field.is_unique or not field.relation_kind:
            return

        if field.relation_kind == "has_many":
            stmt = select(EntityRelationLink).where(
                EntityRelationLink.source_field_id == field.id,
                EntityRelationLink.target_record_id == target_record_id,
                EntityRelationLink.owner_id == self.workspace_id,
            )
            if exclude_record_id:
                stmt = stmt.where(
                    EntityRelationLink.source_record_id != exclude_record_id
                )

            result = await self.session.exec(stmt)
            if result.first():
                raise TracecatValidationError(
                    f"Record is already linked to another {field.field_key}"
                )

    async def validate_backref_field(
        self, field: FieldMetadata, target_entity: EntityMetadata
    ) -> FieldMetadata | None:
        """Validate and retrieve backref field if specified.

        Args:
            field: Field with relation settings
            target_entity: Target entity metadata

        Returns:
            Backref FieldMetadata if exists and valid, None otherwise

        Raises:
            TracecatNotFoundError: If backref field specified but not found
            TracecatValidationError: If backref field is not active
        """
        if not field.relation_backref_field_id:
            return None

        # Get the backref field by ID
        stmt = select(FieldMetadata).where(
            FieldMetadata.id == field.relation_backref_field_id
        )
        result = await self.session.exec(stmt)
        backref_field = result.first()

        if not backref_field:
            raise TracecatNotFoundError(
                f"Backref field with ID '{field.relation_backref_field_id}' not found"
            )

        # Verify it belongs to the target entity
        if backref_field.entity_metadata_id != target_entity.id:
            raise TracecatValidationError(
                f"Backref field does not belong to target entity '{target_entity.name}'"
            )

        if not backref_field.is_active:
            raise TracecatValidationError(
                f"Backref field '{backref_field.field_key}' is not active"
            )

        return backref_field


class ConstraintValidators:
    """Validators for constraint changes on existing data."""

    def __init__(self, session: AsyncSession, workspace_id: str | UUID):
        """Initialize constraint validators.

        Args:
            session: Database session
            workspace_id: Workspace ID for scoping queries
        """
        self.session = session
        self.workspace_id = workspace_id

    async def validate_unique_constraint_change(self, field: FieldMetadata) -> None:
        """Validate that enabling unique constraint won't violate existing data.

        Args:
            field: Field metadata to check

        Raises:
            ValueError: If duplicate values exist
        """
        duplicate_check = text("""
            SELECT field_data->>:field_key as value, COUNT(*) as cnt
            FROM entity_data
            WHERE entity_metadata_id = :entity_id
              AND owner_id = :owner_id
              AND field_data ? :field_key
              AND field_data->>:field_key IS NOT NULL
            GROUP BY field_data->>:field_key
            HAVING COUNT(*) > 1
        """)

        result = await self.session.execute(
            duplicate_check,
            {
                "field_key": field.field_key,
                "entity_id": field.entity_metadata_id,
                "owner_id": self.workspace_id,
            },
        )

        duplicates = result.fetchall()
        if duplicates:
            values = [f"'{row[0]}' ({row[1]} occurrences)" for row in duplicates]
            raise ValueError(
                f"Cannot enable unique constraint. Duplicate values found: {', '.join(values)}"
            )

    async def validate_required_constraint_change(self, field: FieldMetadata) -> None:
        """Validate that enabling required constraint won't violate existing data.

        Args:
            field: Field metadata to check

        Raises:
            ValueError: If null or missing values exist
        """
        null_check = text("""
            SELECT COUNT(*) as cnt
            FROM entity_data
            WHERE entity_metadata_id = :entity_id
              AND owner_id = :owner_id
              AND (
                NOT field_data ? :field_key
                OR field_data->>:field_key IS NULL
              )
        """)

        result = await self.session.execute(
            null_check,
            {
                "field_key": field.field_key,
                "entity_id": field.entity_metadata_id,
                "owner_id": self.workspace_id,
            },
        )

        null_count = result.scalar() or 0
        if null_count > 0:
            raise ValueError(
                f"Cannot enable required constraint. "
                f"{null_count} records have null or missing values for '{field.field_key}'"
            )
