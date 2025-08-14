"""Service layer for custom entities with immutable field schemas."""

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from pydantic import UUID4, BaseModel, ConfigDict, ValidationError, create_model
from pydantic import Field as PydanticField
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import SelectOfScalar

from tracecat.authz.controls import require_access_level
from tracecat.db.schemas import (
    EntityData,
    EntityMetadata,
    EntityRelationLink,
    FieldMetadata,
)
from tracecat.entities.common import (
    format_belongs_to_cache,
    serialize_value,
    validate_relation_settings,
)
from tracecat.entities.enums import RelationKind
from tracecat.entities.models import (
    EntityMetadataCreate,
    FieldMetadataCreate,
    HasManyRelationUpdate,
    RelationOperation,
    RelationSettings,
)
from tracecat.entities.query import EntityQueryBuilder
from tracecat.entities.types import (
    FieldType,
    get_python_type,
)
from tracecat.entities.validation import (
    EntityValidators,
    FieldValidators,
    RecordValidators,
    RelationValidators,
)
from tracecat.logger import logger
from tracecat.service import BaseWorkspaceService
from tracecat.types.auth import AccessLevel
from tracecat.types.exceptions import TracecatNotFoundError


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
        # Initialize validators
        self.entity_validators = EntityValidators(session, self.workspace_id)
        self.field_validators = FieldValidators(session, self.workspace_id)
        self.record_validators = RecordValidators(
            session, str(self.workspace_id), self.query_builder
        )
        self.relation_validators = RelationValidators(session, self.workspace_id)

    # Entity Metadata Operations

    @require_access_level(AccessLevel.ADMIN)
    async def create_entity_type(
        self,
        name: str,
        display_name: str,
        description: str | None = None,
        icon: str | None = None,
    ) -> EntityMetadata:
        """Create a new entity type.

        Args:
            name: Unique identifier for the entity type (immutable)
            display_name: Human-readable name
            description: Optional description
            icon: Optional icon identifier

        Returns:
            Created EntityMetadata

        Raises:
            ValueError: If name already exists or is invalid
        """
        # Validate using Pydantic model
        validated = EntityMetadataCreate(
            name=name,
            display_name=display_name,
            description=description,
            icon=icon,
        )

        # Check uniqueness
        await self.entity_validators.validate_entity_name_unique(name)

        entity = EntityMetadata(
            owner_id=self.workspace_id,
            name=validated.name,
            display_name=validated.display_name,
            description=validated.description,
            icon=validated.icon,
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

    @require_access_level(AccessLevel.ADMIN)
    async def delete_entity_type(self, entity_id: UUID) -> None:
        """Permanently delete an entity type and all associated data.

        Args:
            entity_id: Entity to delete

        Note:
            This is a hard delete - all data will be permanently lost.
            - Deletes all records of this entity type
            - Deletes all fields
            - Deletes all relation links
            - Deletes the entity metadata
        """
        entity = await self.get_entity_type(entity_id)

        # Delete all relation links involving this entity
        # Links where records of this entity are sources
        source_links_stmt = select(EntityRelationLink).where(
            EntityRelationLink.source_entity_metadata_id == entity_id
        )
        source_links_result = await self.session.exec(source_links_stmt)
        for link in source_links_result.all():
            await self.session.delete(link)

        # Links where records of this entity are targets
        target_links_stmt = select(EntityRelationLink).where(
            EntityRelationLink.target_entity_metadata_id == entity_id
        )
        target_links_result = await self.session.exec(target_links_stmt)
        for link in target_links_result.all():
            await self.session.delete(link)

        # Delete all records of this entity type
        records_stmt = select(EntityData).where(
            EntityData.entity_metadata_id == entity_id,
            EntityData.owner_id == self.workspace_id,
        )
        records_result = await self.session.exec(records_stmt)
        for record in records_result.all():
            await self.session.delete(record)

        # Delete all fields (cascade will handle this, but let's be explicit)
        fields_stmt = select(FieldMetadata).where(
            FieldMetadata.entity_metadata_id == entity_id
        )
        fields_result = await self.session.exec(fields_stmt)
        for field in fields_result.all():
            await self.session.delete(field)

        # Delete the entity metadata itself
        await self.session.delete(entity)
        await self.session.commit()

        logger.info(f"Permanently deleted entity type {entity.name}")

    # Field Operations

    @require_access_level(AccessLevel.ADMIN)
    async def create_field(
        self,
        entity_id: UUID,
        field_key: str,
        field_type: FieldType,
        display_name: str,
        description: str | None = None,
        enum_options: list[str] | None = None,
        default_value: Any | None = None,
    ) -> FieldMetadata:
        """Create new field - immutable after creation.

        Args:
            entity_id: Entity metadata ID
            field_key: Unique field key (immutable)
            field_type: Field type (immutable)
            display_name: Human-readable name
            description: Optional description
            enum_options: Options for SELECT/MULTI_SELECT fields
            default_value: Optional default value (only for primitive types)

        Returns:
            Created FieldMetadata

        Raises:
            ValueError: If field_key invalid or already exists
        """
        # Validate entity exists
        await self.get_entity_type(entity_id)

        # Check field key uniqueness
        await self.field_validators.validate_field_key_unique(entity_id, field_key)

        # Validate using Pydantic model
        try:
            validated = FieldMetadataCreate(
                field_key=field_key,
                field_type=field_type,
                display_name=display_name,
                description=description,
                enum_options=enum_options,
                default_value=default_value,
            )
        except ValidationError as e:
            # Convert to ValueError for backward compatibility
            # Extract the first error message for cleaner output
            first_error = e.errors()[0] if e.errors() else {}
            error_msg = first_error.get("msg", str(e))
            raise ValueError(error_msg) from e

        # Validate and serialize default value if provided
        serialized_default = None
        if validated.default_value is not None:
            from tracecat.entities.common import validate_and_serialize_default_value

            serialized_default = validate_and_serialize_default_value(
                validated.default_value, validated.field_type, validated.enum_options
            )

        # Ensure None values are properly passed (not empty strings or "null")
        field = FieldMetadata(
            entity_metadata_id=entity_id,
            field_key=validated.field_key,
            field_type=validated.field_type.value,
            display_name=validated.display_name,
            description=validated.description,
            enum_options=validated.enum_options,
            is_active=True,
            default_value=serialized_default,
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
    async def update_field(
        self,
        field_id: UUID,
        display_name: str | None = None,
        description: str | None = None,
        enum_options: list[str] | None = None,
        default_value: Any | None = None,
    ) -> FieldMetadata:
        """Update field properties.

        Args:
            field_id: Field to update
            display_name: New display name
            description: New description
            enum_options: New options for SELECT/MULTI_SELECT fields
            default_value: Update default value (None to clear, use {} for empty dict)

        Returns:
            Updated FieldMetadata

        Raises:
            ValueError: If validation fails
        """
        field = await self.get_field(field_id)

        if display_name is not None:
            field.display_name = display_name
        if description is not None:
            field.description = description
        if enum_options is not None:
            # Validate enum_options for field type
            if FieldType(field.field_type) in (
                FieldType.SELECT,
                FieldType.MULTI_SELECT,
            ):
                field.enum_options = enum_options
            else:
                raise ValueError(
                    f"Field type {field.field_type} does not support enum_options"
                )

        # Handle default value update
        if default_value is not None:
            # Use the validator from FieldMetadataCreate to validate
            try:
                FieldMetadataCreate(
                    field_key=field.field_key,
                    field_type=FieldType(field.field_type),
                    display_name=field.display_name,
                    enum_options=field.enum_options,
                    default_value=default_value,
                )
            except ValidationError as e:
                # Convert to ValueError for backward compatibility
                # Extract the first error message for cleaner output
                first_error = e.errors()[0] if e.errors() else {}
                error_msg = first_error.get("msg", str(e))
                raise ValueError(error_msg) from e

            # Serialize and store the default value
            field.default_value = serialize_value(
                default_value, FieldType(field.field_type)
            )

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

    @require_access_level(AccessLevel.ADMIN)
    async def delete_field(self, field_id: UUID) -> None:
        """Permanently delete a field and all associated data.

        Args:
            field_id: Field to delete

        Note:
            This is a hard delete - all data will be permanently lost.
            - Removes field from all records
            - Deletes all relation links if it's a relation field
            - Deletes the field metadata
        """
        field = await self.get_field(field_id)

        # If it's a relation field, delete all associated links
        if field.relation_kind:
            # Delete all links where this field is the source
            links_stmt = select(EntityRelationLink).where(
                EntityRelationLink.source_field_id == field_id
            )
            links_result = await self.session.exec(links_stmt)
            for link in links_result.all():
                await self.session.delete(link)

        # Remove field data from all records of this entity
        records_stmt = select(EntityData).where(
            EntityData.entity_metadata_id == field.entity_metadata_id,
            EntityData.owner_id == self.workspace_id,
        )
        records_result = await self.session.exec(records_stmt)
        for record in records_result.all():
            if field.field_key in record.field_data:
                del record.field_data[field.field_key]
                flag_modified(record, "field_data")

        # Delete the field metadata itself
        await self.session.delete(field)
        await self.session.commit()

        logger.info(f"Permanently deleted field {field.field_key}")

    # Relation Field Operations

    @require_access_level(AccessLevel.ADMIN)
    async def create_relation_field(
        self,
        entity_id: UUID,
        field_key: str,
        field_type: FieldType,
        display_name: str,
        relation_settings: RelationSettings,
        description: str | None = None,
    ) -> FieldMetadata:
        """Create a relation field with proper metadata.

        Args:
            entity_id: Entity metadata ID
            field_key: Unique field key
            field_type: Must be RELATION_BELONGS_TO or RELATION_HAS_MANY
            display_name: Human-readable name
            relation_settings: Relation configuration
            description: Optional description

        Returns:
            Created FieldMetadata with relation metadata

        Raises:
            ValueError: If validation fails
            TracecatNotFoundError: If entity or target entity not found
        """
        # Validate entity exists
        await self.get_entity_type(entity_id)

        # Validate relation settings match field type
        is_valid, error = validate_relation_settings(field_type, relation_settings)
        if not is_valid:
            raise ValueError(error)

        # Validate target entity exists and belongs to same owner
        try:
            await self.get_entity_type(relation_settings.target_entity_id)
        except TracecatNotFoundError as err:
            raise ValueError(
                f"Target entity {relation_settings.target_entity_id} not found"
            ) from err

        # Validate relation nesting policy
        (
            is_valid,
            error_msg,
        ) = await self.relation_validators.nesting_validator.validate_relation_creation(
            source_entity_id=entity_id,
            target_entity_id=relation_settings.target_entity_id,
        )
        if not is_valid:
            raise ValueError(error_msg)

        # Validate using Pydantic model
        try:
            validated = FieldMetadataCreate(
                field_key=field_key,
                field_type=field_type,
                display_name=display_name,
                description=description,
                relation_settings=relation_settings,
            )
        except ValidationError as e:
            # Convert to ValueError for backward compatibility
            # Extract the first error message for cleaner output
            first_error = e.errors()[0] if e.errors() else {}
            error_msg = first_error.get("msg", str(e))
            raise ValueError(error_msg) from e

        # Check field key uniqueness
        await self.field_validators.validate_field_key_unique(
            entity_id, validated.field_key
        )

        # Determine relation_kind based on field_type
        relation_kind = (
            RelationKind.ONE_TO_ONE
            if field_type == FieldType.RELATION_BELONGS_TO
            else RelationKind.ONE_TO_MANY
        )

        # Create field with relation metadata
        field = FieldMetadata(
            entity_metadata_id=entity_id,
            field_key=validated.field_key,
            field_type=validated.field_type,
            display_name=validated.display_name,
            description=validated.description,
            is_active=True,
            relation_kind=relation_kind,
            relation_target_entity_id=relation_settings.target_entity_id,
        )

        self.session.add(field)
        await self.session.commit()
        await self.session.refresh(field)
        return field

    # Relation Record Operations

    async def update_belongs_to_relation(
        self,
        source_record_id: UUID,
        field: FieldMetadata,
        target_record_id: UUID | None,
    ) -> None:
        """Update or clear a belongs-to relation.

        Args:
            source_record_id: The record that owns the relation
            field: The belongs_to field metadata
            target_record_id: The target record ID or None to clear

        Raises:
            TracecatNotFoundError: If records not found
            ValueError: If validation fails
        """
        # Validate source record exists
        source_record = await self.get_record(source_record_id)

        # If clearing the relation
        if target_record_id is None:
            # Delete existing link if any
            existing_link_stmt = select(EntityRelationLink).where(
                EntityRelationLink.source_record_id == source_record_id,
                EntityRelationLink.source_field_id == field.id,
            )
            existing_result = await self.session.exec(existing_link_stmt)
            existing_link = existing_result.first()

            if existing_link:
                await self.session.delete(existing_link)

            # Clear from field_data cache if present
            if field.field_key in source_record.field_data:
                del source_record.field_data[field.field_key]
                flag_modified(source_record, "field_data")

            await self.session.commit()
            return

        # Validate target record exists and has same owner
        # Ensure target entity id is present for relation fields
        target_entity_id = field.relation_target_entity_id
        if target_entity_id is None:
            raise ValueError("Relation field is missing target entity id")

        target_record_stmt = select(EntityData).where(
            EntityData.id == target_record_id,
            EntityData.owner_id == self.workspace_id,
            EntityData.entity_metadata_id == target_entity_id,
        )
        target_result = await self.session.exec(target_record_stmt)
        target_record = target_result.first()

        if not target_record:
            raise TracecatNotFoundError(
                f"Target record {target_record_id} not found or doesn't match target entity"
            )

        # Delete existing link if any (for idempotency)
        existing_link_stmt = select(EntityRelationLink).where(
            EntityRelationLink.source_record_id == source_record_id,
            EntityRelationLink.source_field_id == field.id,
        )
        existing_result = await self.session.exec(existing_link_stmt)
        existing_link = existing_result.first()

        if existing_link:
            await self.session.delete(existing_link)

        # Create new link
        new_link = EntityRelationLink(
            owner_id=self.workspace_id,
            source_entity_metadata_id=source_record.entity_metadata_id,
            source_field_id=field.id,
            source_record_id=source_record_id,
            target_entity_metadata_id=cast(UUID4, target_entity_id),
            target_record_id=target_record_id,
        )

        self.session.add(new_link)

        # Optionally cache in field_data for faster reads
        # Get a display value if possible (e.g., name field)
        display_value = None
        if "name" in target_record.field_data:
            display_value = target_record.field_data["name"]
        elif "title" in target_record.field_data:
            display_value = target_record.field_data["title"]

        source_record.field_data[field.field_key] = format_belongs_to_cache(
            target_record_id, display_value
        )
        flag_modified(source_record, "field_data")

        await self.session.commit()

    async def update_has_many_relation(
        self,
        source_record_id: UUID,
        field: FieldMetadata,
        operation: HasManyRelationUpdate,
    ) -> dict[str, int]:
        """Process has-many relation updates in batches.

        Args:
            source_record_id: The record that owns the has_many relation
            field: The has_many field metadata
            operation: The batch operation to perform

        Returns:
            Statistics dict with keys: added, removed, unchanged

        Raises:
            TracecatNotFoundError: If records not found
            ValueError: If validation fails
        """
        # Validate source record exists
        source_record = await self.get_record(source_record_id)

        if source_record.entity_metadata_id != field.entity_metadata_id:
            raise ValueError("Field doesn't belong to the record's entity")

        stats = {"added": 0, "removed": 0, "unchanged": 0}

        # Validate all target records exist and have same owner
        target_entity_id = field.relation_target_entity_id
        if target_entity_id is None:
            raise ValueError("Relation field is missing target entity id")
        if operation.target_ids:
            # Check in batches for performance
            for batch_ids in self._batch_ids(operation.target_ids, batch_size=500):
                # Cast to Any to satisfy type checker for SQLAlchemy expression API
                target_stmt = select(EntityData).where(
                    cast(Any, EntityData.id).in_(batch_ids),
                    EntityData.owner_id == self.workspace_id,
                    EntityData.entity_metadata_id == target_entity_id,
                )
                target_result = await self.session.exec(target_stmt)
                found_ids = {str(record.id) for record in target_result.all()}

                # Convert UUIDs to strings for comparison
                batch_ids_str = {
                    str(id) if isinstance(id, UUID) else id for id in batch_ids
                }
                missing_ids = batch_ids_str - found_ids

                if missing_ids:
                    raise TracecatNotFoundError(
                        f"Target records not found or don't match target entity: {missing_ids}"
                    )

        if operation.operation == RelationOperation.REPLACE:
            # Delete all existing links
            delete_stmt = select(EntityRelationLink).where(
                EntityRelationLink.source_record_id == source_record_id,
                EntityRelationLink.source_field_id == field.id,
            )
            existing_result = await self.session.exec(delete_stmt)
            existing_links = list(existing_result.all())

            for link in existing_links:
                await self.session.delete(link)
            stats["removed"] = len(existing_links)

            # Add new links
            for batch_ids in self._batch_ids(operation.target_ids, batch_size=500):
                for target_id in batch_ids:
                    new_link = EntityRelationLink(
                        owner_id=self.workspace_id,
                        source_entity_metadata_id=field.entity_metadata_id,
                        source_field_id=field.id,
                        source_record_id=source_record_id,
                        target_entity_metadata_id=cast(UUID4, target_entity_id),
                        target_record_id=target_id,
                    )
                    self.session.add(new_link)
                    stats["added"] += 1

        elif operation.operation == RelationOperation.ADD:
            # Get existing links to check for duplicates
            existing_stmt = select(EntityRelationLink.target_record_id).where(
                EntityRelationLink.source_record_id == source_record_id,
                EntityRelationLink.source_field_id == field.id,
            )
            existing_result = await self.session.exec(existing_stmt)
            existing_target_ids = {str(id) for id in existing_result.all()}

            # Add only new links
            for batch_ids in self._batch_ids(operation.target_ids, batch_size=500):
                for target_id in batch_ids:
                    target_id_str = (
                        str(target_id) if isinstance(target_id, UUID) else target_id
                    )
                    if target_id_str not in existing_target_ids:
                        new_link = EntityRelationLink(
                            owner_id=self.workspace_id,
                            source_entity_metadata_id=field.entity_metadata_id,
                            source_field_id=field.id,
                            source_record_id=source_record_id,
                            target_entity_metadata_id=cast(UUID4, target_entity_id),
                            target_record_id=target_id,
                        )
                        self.session.add(new_link)
                        stats["added"] += 1
                    else:
                        stats["unchanged"] += 1

        elif operation.operation == RelationOperation.REMOVE:
            # Delete specified links
            if operation.target_ids:
                for batch_ids in self._batch_ids(operation.target_ids, batch_size=500):
                    delete_stmt = select(EntityRelationLink).where(
                        EntityRelationLink.source_record_id == source_record_id,
                        EntityRelationLink.source_field_id == field.id,
                        cast(Any, EntityRelationLink.target_record_id).in_(batch_ids),
                    )
                    delete_result = await self.session.exec(delete_stmt)
                    links_to_delete = list(delete_result.all())

                    for link in links_to_delete:
                        await self.session.delete(link)
                        stats["removed"] += 1

        await self.session.commit()
        return stats

    def _batch_ids(self, ids: list[UUID], batch_size: int = 500):
        """Split IDs into batches for processing.

        Args:
            ids: List of UUIDs to batch
            batch_size: Size of each batch

        Yields:
            Batches of IDs
        """
        for i in range(0, len(ids), batch_size):
            yield ids[i : i + batch_size]

    async def _process_relation_fields_on_create(
        self,
        record: EntityData,
        relation_fields: dict[str, FieldMetadata],
        relation_data: dict[str, Any],
    ) -> None:
        """Process relation fields when creating a record.

        Args:
            record: The newly created record
            relation_fields: Dictionary of relation field metadata
            relation_data: Dictionary of relation values to process
        """
        for field_key, value in relation_data.items():
            if value is None:
                continue

            field = relation_fields[field_key]

            if field.relation_kind == RelationKind.ONE_TO_ONE:
                # Handle belongs_to relation
                target_id = UUID(value) if isinstance(value, str) else value
                await self.update_belongs_to_relation(
                    source_record_id=record.id,
                    field=field,
                    target_record_id=target_id,
                )
            elif field.relation_kind == RelationKind.ONE_TO_MANY:
                # Handle has_many relation - use REPLACE operation for initial creation
                target_ids = []
                for item in value:
                    target_id = UUID(item) if isinstance(item, str) else item
                    target_ids.append(target_id)

                operation = HasManyRelationUpdate(
                    operation=RelationOperation.REPLACE,
                    target_ids=target_ids,
                )
                await self.update_has_many_relation(
                    source_record_id=record.id,
                    field=field,
                    operation=operation,
                )

    async def handle_record_deletion(
        self,
        record_id: UUID,
        cascade_relations: bool = True,
    ) -> None:
        """Handle cascading deletes for relations.

        Args:
            record_id: Record being deleted
            cascade_relations: Whether to cascade delete related records

        Note:
            - Source record deletion: Links auto-deleted via FK CASCADE
            - Target record deletion: Handles based on cascade_delete setting
        """
        record = await self.get_record(record_id)

        # Find all fields that reference this record's entity as a target
        referencing_fields_stmt = select(FieldMetadata).where(
            FieldMetadata.relation_target_entity_id == record.entity_metadata_id,
            FieldMetadata.is_active,
        )
        referencing_fields_result = await self.session.exec(referencing_fields_stmt)
        referencing_fields = list(referencing_fields_result.all())

        for field in referencing_fields:
            # Find all source records that link to this record
            links_stmt = select(EntityRelationLink).where(
                EntityRelationLink.target_record_id == record_id,
                EntityRelationLink.source_field_id == field.id,
            )
            links_result = await self.session.exec(links_stmt)
            links = list(links_result.all())

            if field.relation_kind == RelationKind.ONE_TO_ONE:
                # For one_to_one: always cascade delete source records
                if cascade_relations:
                    # Delete source records
                    for link in links:
                        source_record = await self.get_record(link.source_record_id)
                        await self.session.delete(source_record)
                else:
                    # Just delete the links (sets relation to NULL)
                    for link in links:
                        await self.session.delete(link)

                        # Clear from field_data cache if present
                        source_record = await self.get_record(link.source_record_id)
                        if field.field_key in source_record.field_data:
                            del source_record.field_data[field.field_key]
                            flag_modified(source_record, "field_data")

            elif field.relation_kind == RelationKind.ONE_TO_MANY:
                # For one_to_many: just delete the links
                # (The source record deletion is handled separately)
                for link in links:
                    await self.session.delete(link)

        # Process in batches if there are many affected records
        if len(links) > 100:
            logger.warning(
                f"Large cascade delete affecting {len(links)} records. "
                "Consider using background job for better performance."
            )

        await self.session.commit()

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

        # Separate relation fields from regular fields
        relation_fields = {f.field_key: f for f in active_fields if f.relation_kind}
        regular_fields = [f for f in active_fields if not f.relation_kind]

        # Extract relation data before processing
        relation_data = {}
        data_without_relations = dict(data)  # Create a copy
        for field_key in list(data_without_relations.keys()):
            if field_key in relation_fields:
                relation_data[field_key] = data_without_relations.pop(field_key)

        # Apply default values for missing regular fields only
        data_with_defaults = dict(data_without_relations)
        for field in regular_fields:
            if (
                field.field_key not in data_with_defaults
                and field.default_value is not None
            ):
                # Validate that the stored default is still valid
                # This protects against corrupted or manually edited defaults
                try:
                    from tracecat.entities.validation import validate_default_value_type

                    validate_default_value_type(
                        field.default_value,
                        FieldType(field.field_type),
                        field.enum_options,
                    )
                    # Default value is already serialized in the database
                    data_with_defaults[field.field_key] = field.default_value
                except Exception as e:
                    # Log warning but don't fail record creation
                    logger.warning(
                        f"Invalid default value for field '{field.field_key}' "
                        f"(type: {field.field_type}): {e}. Skipping default."
                    )
                    # Skip this default value rather than fail the entire record creation

        # Validate all data (regular + relation fields)
        all_data_to_validate = {**data_with_defaults, **relation_data}
        validated_data = await self.record_validators.validate_record_data(
            all_data_to_validate, active_fields
        )

        # Serialize only non-relation values
        serialized_data = {}
        field_map = {f.field_key: f for f in active_fields}
        for key, value in validated_data.items():
            if key in field_map and not field_map[key].relation_kind:
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

        # Now handle relation fields if any were provided
        if relation_data:
            await self._process_relation_fields_on_create(
                record, relation_fields, relation_data
            )

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
        validated_updates = await self.record_validators.validate_record_data(
            updates, active_fields
        )

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
            py_type = get_python_type(FieldType(field.field_type))

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
