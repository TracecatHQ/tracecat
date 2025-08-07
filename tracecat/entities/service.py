"""Service layer for custom entities with immutable field schemas."""

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError, create_model
from pydantic import Field as PydanticField
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import SelectOfScalar

from tracecat.authz.controls import require_access_level
from tracecat.db.schemas import EntityData, EntityMetadata, FieldMetadata
from tracecat.entities.common import (
    serialize_value,
    validate_value_for_type,
)
from tracecat.entities.models import (
    EntityMetadataCreate,
    FieldMetadataCreate,
)
from tracecat.entities.query import EntityQueryBuilder
from tracecat.entities.types import (
    FieldType,
    get_python_type,
    validate_flat_structure,
)
from tracecat.logger import logger
from tracecat.service import BaseWorkspaceService
from tracecat.types.auth import AccessLevel
from tracecat.types.exceptions import (
    TracecatNotFoundError,
    TracecatValidationError,
)


class CustomEntitiesService(BaseWorkspaceService):
    """Service for custom entities with immutable field schemas.

    v1 Features:
    - Fields are immutable after creation (key and type cannot change)
    - All fields are nullable (no required fields)
    - Soft delete only (data preserved)
    - Flat JSONB structure (no nested objects)
    """

    service_name = "custom_entities"

    def __init__(self, session: AsyncSession, role=None):
        """Initialize service with session and role."""
        super().__init__(session, role)
        self.query_builder = EntityQueryBuilder(session)

    # Entity Metadata Operations

    @require_access_level(AccessLevel.ADMIN)
    async def create_entity_type(
        self,
        name: str,
        display_name: str,
        description: str | None = None,
        icon: str | None = None,
        settings: dict[str, Any] | None = None,
    ) -> EntityMetadata:
        """Create a new entity type.

        Args:
            name: Unique identifier for the entity type (immutable)
            display_name: Human-readable name
            description: Optional description
            icon: Optional icon identifier
            settings: Optional settings dict

        Returns:
            Created EntityMetadata

        Raises:
            ValueError: If name already exists or is invalid
        """
        # Validate using Pydantic model
        try:
            validated = EntityMetadataCreate(
                name=name,
                display_name=display_name,
                description=description,
                icon=icon,
                settings=settings or {},
            )
        except ValidationError as e:
            # Extract the first validation error message
            if e.errors():
                first_error = e.errors()[0]
                msg = first_error.get("msg", "Invalid entity name format")
                # Remove "Value error, " prefix if present
                if msg.startswith("Value error, "):
                    msg = msg[13:]  # len("Value error, ") = 13
                raise ValueError(msg) from e
            raise ValueError("Invalid entity name format") from e

        # Check uniqueness
        existing = await self.session.exec(
            select(EntityMetadata).where(
                EntityMetadata.owner_id == self.workspace_id,
                EntityMetadata.name == name,
            )
        )
        if existing.first():
            raise ValueError(f"Entity type '{name}' already exists")

        entity = EntityMetadata.model_construct(
            owner_id=self.workspace_id,
            name=validated.name,
            display_name=validated.display_name,
            description=validated.description,
            icon=validated.icon,
            settings=validated.settings,
            is_active=True,
        )

        self.session.add(entity)
        await self.session.commit()
        await self.session.refresh(entity)
        return entity

    async def get_entity_type(self, entity_id: UUID) -> EntityMetadata:
        """Get entity type by ID.

        Args:
            entity_id: Entity metadata ID

        Returns:
            EntityMetadata

        Raises:
            TracecatNotFoundError: If not found
        """
        stmt = select(EntityMetadata).where(
            EntityMetadata.id == entity_id, EntityMetadata.owner_id == self.workspace_id
        )
        result = await self.session.exec(stmt)
        entity = result.first()

        if not entity:
            raise TracecatNotFoundError(f"Entity type {entity_id} not found")

        return entity

    async def list_entity_types(
        self, include_inactive: bool = False
    ) -> list[EntityMetadata]:
        """List all entity types.

        Args:
            include_inactive: Whether to include soft-deleted entities

        Returns:
            List of EntityMetadata
        """
        stmt = select(EntityMetadata).where(
            EntityMetadata.owner_id == self.workspace_id
        )

        if not include_inactive:
            stmt = stmt.where(EntityMetadata.is_active)

        result = await self.session.exec(stmt)
        return list(result.all())

    # Field Operations

    @require_access_level(AccessLevel.ADMIN)
    async def create_field(
        self,
        entity_id: UUID,
        field_key: str,
        field_type: FieldType,
        display_name: str,
        description: str | None = None,
        settings: dict[str, Any] | None = None,
    ) -> FieldMetadata:
        """Create new field - immutable after creation.

        Args:
            entity_id: Entity metadata ID
            field_key: Unique field key (immutable)
            field_type: Field type (immutable)
            display_name: Human-readable name
            description: Optional description
            settings: Optional field settings

        Returns:
            Created FieldMetadata

        Raises:
            ValueError: If field_key invalid or already exists
        """
        # Validate entity exists
        await self.get_entity_type(entity_id)

        # Validate using Pydantic model
        try:
            validated = FieldMetadataCreate(
                field_key=field_key,
                field_type=field_type,
                display_name=display_name,
                description=description,
                field_settings=settings or {},
            )
        except ValidationError as e:
            # Extract the first validation error message
            if e.errors():
                first_error = e.errors()[0]
                msg = first_error.get("msg", "Invalid field key format")
                # Remove "Value error, " prefix if present
                if msg.startswith("Value error, "):
                    msg = msg[13:]  # len("Value error, ") = 13
                raise ValueError(msg) from e
            raise ValueError("Invalid field key format") from e

        # Check uniqueness
        existing = await self.session.exec(
            select(FieldMetadata).where(
                FieldMetadata.entity_metadata_id == entity_id,
                FieldMetadata.field_key == validated.field_key,
            )
        )
        if existing.first():
            raise ValueError(f"Field key '{validated.field_key}' already exists")

        # Validate settings don't allow nested structures
        if settings and "allow_nested" in settings:
            raise ValueError("Nested structures not supported in v1")

        field = FieldMetadata(
            entity_metadata_id=entity_id,
            field_key=validated.field_key,
            field_type=validated.field_type,
            display_name=validated.display_name,
            description=validated.description,
            field_settings=validated.field_settings,
            is_active=True,
            is_required=False,  # Always false in v1
        )

        self.session.add(field)
        await self.session.commit()
        await self.session.refresh(field)
        return field

    async def get_field(self, field_id: UUID) -> FieldMetadata:
        """Get field by ID.

        Args:
            field_id: Field metadata ID

        Returns:
            FieldMetadata

        Raises:
            TracecatNotFoundError: If not found
        """
        stmt = select(FieldMetadata).where(FieldMetadata.id == field_id)
        result = await self.session.exec(stmt)
        field = result.first()

        if not field:
            raise TracecatNotFoundError(f"Field {field_id} not found")

        return field

    async def list_fields(
        self, entity_id: UUID, include_inactive: bool = False
    ) -> list[FieldMetadata]:
        """List fields for an entity.

        Args:
            entity_id: Entity metadata ID
            include_inactive: Whether to include soft-deleted fields

        Returns:
            List of FieldMetadata
        """
        stmt = select(FieldMetadata).where(
            FieldMetadata.entity_metadata_id == entity_id
        )

        if not include_inactive:
            stmt = stmt.where(FieldMetadata.is_active)

        result = await self.session.exec(stmt)
        return list(result.all())

    @require_access_level(AccessLevel.ADMIN)
    async def update_field_display(
        self,
        field_id: UUID,
        display_name: str | None = None,
        description: str | None = None,
        settings: dict[str, Any] | None = None,
    ) -> FieldMetadata:
        """Update only display properties - no schema changes.

        Args:
            field_id: Field to update
            display_name: New display name
            description: New description
            settings: New settings (validation constraints)

        Returns:
            Updated FieldMetadata
        """
        field = await self.get_field(field_id)

        if display_name is not None:
            field.display_name = display_name
        if description is not None:
            field.description = description
        if settings is not None:
            # Validate settings for field type
            if "allow_nested" in settings:
                raise ValueError("Nested structures not supported in v1")
            field.field_settings = settings

        await self.session.commit()
        await self.session.refresh(field)
        return field

    @require_access_level(AccessLevel.ADMIN)
    async def deactivate_field(self, field_id: UUID) -> FieldMetadata:
        """Soft delete - data preserved in JSONB.

        Args:
            field_id: Field to deactivate

        Returns:
            Deactivated FieldMetadata

        Raises:
            ValueError: If field already inactive
        """
        field = await self.get_field(field_id)

        if not field.is_active:
            raise ValueError("Field is already inactive")

        field.is_active = False
        field.deactivated_at = datetime.now(UTC)

        await self.session.commit()
        await self.session.refresh(field)

        logger.info(f"Deactivated field {field.field_key}")
        return field

    @require_access_level(AccessLevel.ADMIN)
    async def reactivate_field(self, field_id: UUID) -> FieldMetadata:
        """Reactivate soft-deleted field.

        Args:
            field_id: Field to reactivate

        Returns:
            Reactivated FieldMetadata

        Raises:
            ValueError: If field already active
        """
        field = await self.get_field(field_id)

        if field.is_active:
            raise ValueError("Field is already active")

        field.is_active = True
        field.deactivated_at = None

        await self.session.commit()
        await self.session.refresh(field)

        logger.info(f"Reactivated field {field.field_key}")
        return field

    # Data Operations

    async def create_record(self, entity_id: UUID, data: dict[str, Any]) -> EntityData:
        """Create a new entity record.

        Args:
            entity_id: Entity metadata ID
            data: Field data (validated against active fields)

        Returns:
            Created EntityData

        Raises:
            ValidationError: If data invalid
            ValueError: If contains nested objects
        """
        # Get active fields
        active_fields = await self.list_fields(entity_id, include_inactive=False)

        # Validate data against active fields (includes flat structure check)
        validated_data = await self._validate_record_data(data, active_fields)

        # Serialize values
        serialized_data = {}
        field_map = {f.field_key: f for f in active_fields}
        for key, value in validated_data.items():
            if key in field_map:
                serialized_data[key] = serialize_value(
                    value, FieldType(field_map[key].field_type)
                )

        # Create record
        record = EntityData(
            entity_metadata_id=entity_id,
            owner_id=self.workspace_id,
            field_data=serialized_data,
        )

        self.session.add(record)
        await self.session.commit()
        await self.session.refresh(record)
        return record

    async def get_record(self, record_id: UUID) -> EntityData:
        """Get entity record by ID.

        Args:
            record_id: Record ID

        Returns:
            EntityData

        Raises:
            TracecatNotFoundError: If not found
        """
        stmt = select(EntityData).where(
            EntityData.id == record_id, EntityData.owner_id == self.workspace_id
        )
        result = await self.session.exec(stmt)
        record = result.first()

        if not record:
            raise TracecatNotFoundError(f"Record {record_id} not found")

        return record

    async def update_record(
        self, record_id: UUID, updates: dict[str, Any]
    ) -> EntityData:
        """Update entity record.

        Args:
            record_id: Record to update
            updates: Field updates (validated against active fields)

        Returns:
            Updated EntityData

        Raises:
            ValidationError: If updates invalid
        """
        record = await self.get_record(record_id)

        # Get active fields for validation
        active_fields = await self.list_fields(
            record.entity_metadata_id, include_inactive=False
        )

        # Validate updates (includes flat structure check)
        validated_updates = await self._validate_record_data(updates, active_fields)

        # Serialize and merge updates
        field_map = {f.field_key: f for f in active_fields}
        for key, value in validated_updates.items():
            if key in field_map:
                record.field_data[key] = serialize_value(
                    value, FieldType(field_map[key].field_type)
                )

        # Mark the field_data as modified for SQLAlchemy to detect the change
        flag_modified(record, "field_data")

        await self.session.commit()
        await self.session.refresh(record)
        return record

    async def delete_record(self, record_id: UUID) -> None:
        """Delete entity record.

        Args:
            record_id: Record to delete
        """
        record = await self.get_record(record_id)
        await self.session.delete(record)
        await self.session.commit()

    async def query_records(
        self,
        entity_id: UUID,
        filters: list[dict[str, Any]] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[EntityData]:
        """Query entity records with filters.

        Args:
            entity_id: Entity metadata ID
            filters: Optional filter specifications
            limit: Max records to return
            offset: Number of records to skip

        Returns:
            List of EntityData matching filters
        """
        # Validate entity exists
        await self.get_entity_type(entity_id)

        # Build base query
        stmt = select(EntityData).where(
            EntityData.entity_metadata_id == entity_id,
            EntityData.owner_id == self.workspace_id,
        )

        # Apply filters if provided
        if filters:
            stmt = await self.query_builder.build_query(stmt, entity_id, filters)

        # Apply pagination
        stmt = stmt.limit(limit).offset(offset)

        result = await self.session.exec(cast(SelectOfScalar[EntityData], stmt))
        return list(result.all())

    # Helper Methods

    async def _validate_record_data(
        self, data: dict[str, Any], fields: list[FieldMetadata]
    ) -> dict[str, Any]:
        """Validate record data against field definitions.

        Args:
            data: Data to validate
            fields: Field definitions

        Returns:
            Validated data (with inactive fields removed)

        Raises:
            TracecatValidationError: If validation fails
        """
        active_field_keys = {f.field_key for f in fields if f.is_active}
        field_map = {f.field_key: f for f in fields if f.is_active}

        # Remove inactive fields from data
        validated = {}
        errors = []

        for key, value in data.items():
            if key not in active_field_keys:
                # Skip inactive/unknown fields
                continue

            # Check flat structure first (more fundamental requirement)
            if not validate_flat_structure(value):
                errors.append(f"Field '{key}': Nested objects not allowed")

            field = field_map[key]
            is_valid, error = validate_value_for_type(
                value, FieldType(field.field_type), field.field_settings
            )

            if not is_valid:
                errors.append(f"{key}: {error}")
            else:
                validated[key] = value

        if errors:
            raise TracecatValidationError("; ".join(errors))

        return validated

    def get_active_fields_model(self, fields: list[FieldMetadata]) -> type[BaseModel]:
        """Generate Pydantic model for active fields only.

        Args:
            fields: Field definitions

        Returns:
            Dynamically created Pydantic model class
        """
        field_definitions = {}

        for field in fields:
            if not field.is_active:
                continue  # Skip inactive fields

            # Get Python type for field
            py_type = get_python_type(FieldType(field.field_type), is_required=False)

            # Create field with description
            field_definitions[field.field_key] = (
                py_type,
                PydanticField(default=None, description=field.description),
            )

        # Create model with extra="forbid" to reject inactive fields
        return create_model(
            "EntityDataModel",
            __config__=ConfigDict(extra="forbid"),
            **field_definitions,
        )
