"""Service layer for custom entities with immutable field schemas."""

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError, create_model
from pydantic import Field as PydanticField
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.authz.controls import require_access_level
from tracecat.db.schemas import (
    Entity,
    FieldMetadata,
    Record,
    RecordRelationLink,
)
from tracecat.entities.common import (
    serialize_value,
    validate_relation_settings,
)
from tracecat.entities.enums import RelationKind
from tracecat.entities.models import (
    EntityCreate,
    EntitySchemaResult,
    FieldMetadataCreate,
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
    validate_default_value_type,
)
from tracecat.logger import logger
from tracecat.service import BaseWorkspaceService
from tracecat.types.auth import AccessLevel, Role
from tracecat.types.exceptions import TracecatNotFoundError


class CustomEntitiesService(BaseWorkspaceService):
    """Service for custom entities with immutable field schemas.

    v1 Features:
    - Fields are immutable after creation (key and type cannot change)
    - All fields are nullable (no required fields)
    - Soft delete only (data preserved)
    - JSON field type supports nested objects (up to 3 levels deep)
    """

    service_name = "custom_entities"

    def __init__(self, session: AsyncSession, role: Role | None = None):
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

    # Entity Operations

    @require_access_level(AccessLevel.ADMIN)
    async def create_entity(
        self,
        name: str,
        display_name: str,
        description: str | None = None,
        icon: str | None = None,
    ) -> Entity:
        """Create a new entity.

        Args:
            name: Unique identifier for the entity (immutable)
            display_name: Human-readable name
            description: Optional description
            icon: Optional icon identifier

        Returns:
            Created Entity

        Raises:
            ValueError: If name already exists or is invalid
        """
        # Validate using Pydantic model
        validated = EntityCreate(
            name=name,
            display_name=display_name,
            description=description,
            icon=icon,
        )

        # Check uniqueness
        await self.entity_validators.validate_entity_name_unique(name)

        entity = Entity(
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

    async def get_entity(self, entity_id: UUID) -> Entity:
        """Get entity by ID.

        Args:
            entity_id: Entity metadata ID

        Returns:
            Entity

        Raises:
            TracecatNotFoundError: If not found
        """
        stmt = select(Entity).where(
            Entity.id == entity_id, Entity.owner_id == self.workspace_id
        )
        result = await self.session.exec(stmt)
        entity = result.first()

        if not entity:
            raise TracecatNotFoundError(f"Entity {entity_id} not found")

        return entity

    async def get_entity_by_name(self, name: str) -> Entity:
        """Get entity by name (slug).

        Args:
            name: Entity type name/slug (e.g., "customer", "incident")

        Returns:
            Entity

        Raises:
            TracecatNotFoundError: If not found
        """
        stmt = select(Entity).where(
            Entity.name == name,
            Entity.owner_id == self.workspace_id,
            Entity.is_active,
        )
        result = await self.session.exec(stmt)
        entity = result.first()

        if not entity:
            raise TracecatNotFoundError(f"Entity '{name}' not found")

        return entity

    async def list_entities(self, include_inactive: bool = False) -> list[Entity]:
        """List all entities.

        Args:
            include_inactive: Whether to include soft-deleted entities

        Returns:
            List of Entity
        """
        stmt = select(Entity).where(Entity.owner_id == self.workspace_id)

        if not include_inactive:
            stmt = stmt.where(Entity.is_active)

        result = await self.session.exec(stmt)
        return list(result.all())

    @require_access_level(AccessLevel.ADMIN)
    async def update_entity(
        self,
        entity_id: UUID,
        display_name: str | None = None,
        description: str | None = None,
        icon: str | None = None,
    ) -> Entity:
        """Update entity display properties.

        Args:
            entity_id: Entity to update
            display_name: New display name
            description: New description
            icon: New icon

        Returns:
            Updated Entity

        Raises:
            TracecatNotFoundError: If entity not found
        """
        entity = await self.get_entity(entity_id)

        if display_name is not None:
            entity.display_name = display_name
        if description is not None:
            entity.description = description
        if icon is not None:
            entity.icon = icon

        await self.session.commit()
        await self.session.refresh(entity)
        return entity

    @require_access_level(AccessLevel.ADMIN)
    async def deactivate_entity(self, entity_id: UUID) -> Entity:
        """Soft delete entity.

        Args:
            entity_id: Entity to deactivate

        Returns:
            Deactivated Entity

        Raises:
            TracecatNotFoundError: If entity not found
            ValueError: If entity already inactive
        """
        entity = await self.get_entity(entity_id)

        if not entity.is_active:
            raise ValueError("Entity is already inactive")

        entity.is_active = False
        await self.session.commit()
        await self.session.refresh(entity)

        logger.info(f"Deactivated entity {entity.name}")
        return entity

    @require_access_level(AccessLevel.ADMIN)
    async def reactivate_entity(self, entity_id: UUID) -> Entity:
        """Reactivate soft-deleted entity.

        Args:
            entity_id: Entity to reactivate

        Returns:
            Reactivated Entity

        Raises:
            TracecatNotFoundError: If entity not found
            ValueError: If entity already active
        """
        entity = await self.get_entity(entity_id)

        if entity.is_active:
            raise ValueError("Entity is already active")

        entity.is_active = True
        await self.session.commit()
        await self.session.refresh(entity)

        logger.info(f"Reactivated entity {entity.name}")
        return entity

    @require_access_level(AccessLevel.ADMIN)
    async def delete_entity(self, entity_id: UUID) -> None:
        """Permanently delete an entity and all associated data.

        Args:
            entity_id: Entity to delete

        Note:
            This is a hard delete - all data will be permanently lost.
            - Deletes all records of this entity
            - Deletes all fields
            - Deletes all relation links
            - Deletes the entity metadata
        """
        entity = await self.get_entity(entity_id)

        # Delete all relation links involving this entity
        # Links where records of this entity are sources
        source_links_stmt = select(RecordRelationLink).where(
            RecordRelationLink.source_entity_id == entity_id
        )
        source_links_result = await self.session.exec(source_links_stmt)
        for link in source_links_result.all():
            await self.session.delete(link)

        # Links where records of this entity are targets
        target_links_stmt = select(RecordRelationLink).where(
            RecordRelationLink.target_entity_id == entity_id
        )
        target_links_result = await self.session.exec(target_links_stmt)
        for link in target_links_result.all():
            await self.session.delete(link)

        # Delete all records of this entity
        records_stmt = select(Record).where(
            Record.entity_id == entity_id,
            Record.owner_id == self.workspace_id,
        )
        records_result = await self.session.exec(records_stmt)
        for record in records_result.all():
            await self.session.delete(record)

        # Delete all fields (cascade will handle this, but let's be explicit)
        fields_stmt = select(FieldMetadata).where(FieldMetadata.entity_id == entity_id)
        fields_result = await self.session.exec(fields_stmt)
        for field in fields_result.all():
            await self.session.delete(field)

        # Delete the entity metadata itself
        await self.session.delete(entity)
        await self.session.commit()

        logger.info(f"Permanently deleted entity {entity.name}")

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
        await self.get_entity(entity_id)

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
            entity_id=entity_id,
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

    async def get_field_by_key(
        self, entity_id: UUID, field_key: str
    ) -> FieldMetadata | None:
        """Get field by entity ID and field key.

        Args:
            entity_id: Entity metadata ID
            field_key: Field key

        Returns:
            FieldMetadata or None if not found
        """
        stmt = select(FieldMetadata).where(
            FieldMetadata.entity_id == entity_id,
            FieldMetadata.field_key == field_key,
            FieldMetadata.is_active,
        )
        result = await self.session.exec(stmt)
        return result.first()

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
        stmt = select(FieldMetadata).where(FieldMetadata.entity_id == entity_id)

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
            links_stmt = select(RecordRelationLink).where(
                RecordRelationLink.source_field_id == field_id
            )
            links_result = await self.session.exec(links_stmt)
            for link in links_result.all():
                await self.session.delete(link)

        # Remove field data from all records of this entity
        records_stmt = select(Record).where(
            Record.entity_id == field.entity_id,
            Record.owner_id == self.workspace_id,
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
        await self.get_entity(entity_id)

        # Validate relation settings match field type
        is_valid, error = validate_relation_settings(field_type, relation_settings)
        if not is_valid:
            raise ValueError(error)

        # Validate target entity exists and belongs to same owner
        try:
            await self.get_entity(relation_settings.target_entity_id)
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
            entity_id=entity_id,
            field_key=validated.field_key,
            field_type=validated.field_type,
            display_name=validated.display_name,
            description=validated.description,
            is_active=True,
            relation_kind=relation_kind,
            target_entity_id=relation_settings.target_entity_id,
        )

        self.session.add(field)
        await self.session.commit()
        await self.session.refresh(field)
        return field

    # Relation handling helpers

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
            FieldMetadata.target_entity_id == record.entity_id,
            FieldMetadata.is_active,
        )
        referencing_fields_result = await self.session.exec(referencing_fields_stmt)
        referencing_fields = list(referencing_fields_result.all())

        for field in referencing_fields:
            # Find all source records that link to this record
            links_stmt = select(RecordRelationLink).where(
                RecordRelationLink.target_record_id == record_id,
                RecordRelationLink.source_field_id == field.id,
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

    async def create_record(self, entity_id: UUID, data: dict[str, Any]) -> Record:
        """Create a new entity record."""
        active_fields = await self.list_fields(entity_id, include_inactive=False)

        relation_fields, regular_fields, field_map = (
            self._split_relation_and_regular_fields(active_fields)
        )

        relation_data, data_without_relations = self._extract_relation_data(
            data, relation_fields
        )

        data_with_defaults = self._apply_default_values(
            data_without_relations, regular_fields
        )

        all_data_to_validate = {**data_with_defaults, **relation_data}
        validated_data = await self.record_validators.validate_record_data(
            all_data_to_validate, active_fields
        )

        serialized_data = self._serialize_non_relation_values(validated_data, field_map)

        record = Record(
            entity_id=entity_id,
            owner_id=self.workspace_id,
            field_data=serialized_data,
        )

        self.session.add(record)
        await self.session.commit()
        await self.session.refresh(record)

        if relation_data:
            await self._create_relation_links(
                entity_id=entity_id,
                record=record,
                relation_data=relation_data,
                relation_fields=relation_fields,
            )
            await self.session.commit()

        return record

    # Internal helpers for record creation
    def _split_relation_and_regular_fields(
        self, fields: list[FieldMetadata]
    ) -> tuple[dict[str, FieldMetadata], list[FieldMetadata], dict[str, FieldMetadata]]:
        relation_fields = {f.field_key: f for f in fields if f.relation_kind}
        regular_fields = [f for f in fields if not f.relation_kind]
        field_map = {f.field_key: f for f in fields}
        return relation_fields, regular_fields, field_map

    def _extract_relation_data(
        self, data: dict[str, Any], relation_fields: dict[str, FieldMetadata]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        relation_data: dict[str, Any] = {}
        data_without_relations = dict(data)
        for field_key in list(data_without_relations.keys()):
            if field_key in relation_fields:
                relation_data[field_key] = data_without_relations.pop(field_key)
        return relation_data, data_without_relations

    def _apply_default_values(
        self, data: dict[str, Any], regular_fields: list[FieldMetadata]
    ) -> dict[str, Any]:
        data_with_defaults = dict(data)
        for field in regular_fields:
            if field.field_key in data_with_defaults or field.default_value is None:
                continue
            try:
                validate_default_value_type(
                    field.default_value,
                    FieldType(field.field_type),
                    field.enum_options,
                )
                data_with_defaults[field.field_key] = field.default_value
            except Exception as e:  # Validation failure shouldn't block record creation
                logger.warning(
                    f"Invalid default value for field '{field.field_key}' "
                    f"(type: {field.field_type}): {e}. Skipping default."
                )
        return data_with_defaults

    def _serialize_non_relation_values(
        self, validated_data: dict[str, Any], field_map: dict[str, FieldMetadata]
    ) -> dict[str, Any]:
        serialized_data: dict[str, Any] = {}
        for key, value in validated_data.items():
            field = field_map.get(key)
            if field is None or field.relation_kind:
                continue
            serialized_data[key] = serialize_value(value, FieldType(field.field_type))
        return serialized_data

    async def _create_relation_links(
        self,
        *,
        entity_id: UUID,
        record: Record,
        relation_data: dict[str, Any],
        relation_fields: dict[str, FieldMetadata],
    ) -> None:
        for field_key, value in relation_data.items():
            if value is None:
                continue

            field = relation_fields[field_key]
            target_entity_id = field.target_entity_id

            if target_entity_id is None:
                raise ValueError(
                    f"Relation field {field_key} is missing target entity id"
                )

            # Handle inline record creation for dicts
            target_ids = await self._process_relation_value(
                field, value, target_entity_id
            )
            if not target_ids:
                continue

            # v1: Do not cache relation data in field_data; only manage links

            for target_id in target_ids:
                link = RecordRelationLink(
                    owner_id=self.workspace_id,
                    source_entity_id=entity_id,
                    source_field_id=field.id,
                    source_record_id=record.id,
                    target_entity_id=target_entity_id,  # Already a UUID
                    target_record_id=target_id,
                )
                self.session.add(link)

    async def _process_relation_value(
        self, field: FieldMetadata, value: Any, target_entity_id: UUID
    ) -> list[UUID]:
        """Process relation value, creating records for dicts or validating UUIDs.

        Args:
            field: Field metadata with relation info
            value: Value to process (UUID, string, dict, or list)
            target_entity_id: Target entity ID for the relation

        Returns:
            List of target record IDs
        """
        if field.relation_kind == RelationKind.ONE_TO_ONE:
            if value is None:
                return []

            # Handle dict for inline creation
            if isinstance(value, dict):
                # Create new record for the target entity
                created_record = await self.create_record(target_entity_id, value)
                return [created_record.id]

            # Handle UUID or string
            target_id = UUID(value) if isinstance(value, str) else value
            await self._validate_target_records_exist([target_id], target_entity_id)
            return [target_id]

        # ONE_TO_MANY
        ids: list[UUID] = []
        for item in value:
            if isinstance(item, dict):
                # Create new record for the target entity
                created_record = await self.create_record(target_entity_id, item)
                ids.append(created_record.id)
            else:
                # Handle UUID or string
                target_id = UUID(item) if isinstance(item, str) else item
                ids.append(target_id)

        # Validate all non-dict UUIDs exist
        non_created_ids = [
            UUID(item) if isinstance(item, str) else item
            for item in value
            if not isinstance(item, dict)
        ]
        if non_created_ids:
            await self._validate_target_records_exist(non_created_ids, target_entity_id)

        return ids

    def _normalize_target_ids(self, field: FieldMetadata, value: Any) -> list[UUID]:
        if field.relation_kind == RelationKind.ONE_TO_ONE:
            if value is None:
                return []
            return [UUID(value) if isinstance(value, str) else value]
        # ONE_TO_MANY
        ids: list[UUID] = []
        for item in value:
            ids.append(UUID(item) if isinstance(item, str) else item)
        return ids

    async def _validate_target_records_exist(
        self, target_ids: list[UUID], target_entity_id: UUID
    ) -> None:
        if not target_ids:
            return
        # pyright cannot infer SQLAlchemy column methods on SQLModel fields
        id_column = cast(Any, Record.id)
        stmt = select(Record.id).where(
            id_column.in_(target_ids),
            Record.owner_id == self.workspace_id,
            Record.entity_id == target_entity_id,
        )
        result = await self.session.exec(stmt)
        found_ids = set(result.all())
        missing = set(target_ids) - found_ids
        if missing:
            # Show only a small sample to avoid huge error messages
            sample = list(missing)[:3]
            raise TracecatNotFoundError(
                f"Target record(s) {sample}{' and more' if len(missing) > 3 else ''} "
                "not found or don't match target entity"
            )

    async def get_record(self, record_id: UUID) -> Record:
        """Get entity record by ID.

        Args:
            record_id: Record ID

        Returns:
            Record

        Raises:
            TracecatNotFoundError: If not found
        """
        stmt = select(Record).where(
            Record.id == record_id, Record.owner_id == self.workspace_id
        )
        result = await self.session.exec(stmt)
        record = result.first()

        if not record:
            raise TracecatNotFoundError(f"Record {record_id} not found")

        return record

    async def update_record(self, record_id: UUID, updates: dict[str, Any]) -> Record:
        """Update entity record.

        Args:
            record_id: Record to update
            updates: Field updates (validated against active fields)

        Returns:
            Updated Record

        Raises:
            ValidationError: If updates invalid
        """
        record = await self.get_record(record_id)

        # Get active fields for validation
        active_fields = await self.list_fields(record.entity_id, include_inactive=False)

        # Validate updates (includes flat structure check)
        validated_updates = await self.record_validators.validate_record_data(
            updates, active_fields
        )

        # Serialize and merge updates, but skip relation fields
        # Relations are immutable after creation and stored as links only
        field_map = {f.field_key: f for f in active_fields}
        for key, value in validated_updates.items():
            field = field_map.get(key)
            if field is None or field.relation_kind:
                # Skip relation fields
                continue
            record.field_data[key] = serialize_value(value, FieldType(field.field_type))

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
    ) -> list[Record]:
        """Query entity records with filters.

        Args:
            entity_id: Entity metadata ID
            filters: Optional filter specifications
            limit: Max records to return
            offset: Number of records to skip

        Returns:
            List of Record matching filters
        """
        # Validate entity exists
        await self.get_entity(entity_id)

        # Build base query
        stmt = select(Record).where(
            Record.entity_id == entity_id,
            Record.owner_id == self.workspace_id,
        )

        # Apply filters if provided
        if filters:
            stmt = await self.query_builder.build_query(stmt, entity_id, filters)

        # Apply pagination
        stmt = stmt.limit(limit).offset(offset)

        result = await self.session.exec(stmt)
        return list(result.all())

    async def get_record_by_slug(
        self, entity_id: UUID, slug_value: str, slug_field: str = "name"
    ) -> Record:
        """Get a single record by slug field value.

        Args:
            entity_id: Entity metadata ID
            slug_value: The slug value to search for
            slug_field: Field to use as slug (default: "name", can be "title", etc.)

        Returns:
            Record record

        Raises:
            TracecatNotFoundError: If no record found
            ValueError: If multiple records found (slug not unique)
        """
        # Validate entity exists
        await self.get_entity(entity_id)

        # Build query using the slug_equals method
        slug_condition = await self.query_builder.slug_equals(
            entity_id, slug_field, slug_value
        )

        stmt = select(Record).where(
            Record.entity_id == entity_id,
            Record.owner_id == self.workspace_id,
            slug_condition,
        )

        result = await self.session.exec(stmt)
        records = list(result.all())

        if not records:
            raise TracecatNotFoundError(
                f"No record found with {slug_field}='{slug_value}' in entity {entity_id}"
            )

        if len(records) > 1:
            raise ValueError(
                f"Multiple records found with {slug_field}='{slug_value}'. "
                f"Slug field '{slug_field}' is not unique for this entity."
            )

        return records[0]

    async def query_records_by_slug(
        self,
        entity_id: UUID,
        slug_pattern: str,
        slug_field: str = "name",
        limit: int = 100,
        offset: int = 0,
    ) -> list[Record]:
        """Query records by slug field pattern.

        Args:
            entity_id: Entity metadata ID
            slug_pattern: Pattern to match (supports % wildcards)
            slug_field: Field to search (default: "name")
            limit: Max records to return
            offset: Number of records to skip

        Returns:
            List of matching EntityData records
        """
        # Validate entity exists
        await self.get_entity(entity_id)

        # Build query using the slug_matches method
        slug_condition = await self.query_builder.slug_matches(
            entity_id, slug_field, slug_pattern
        )

        stmt = (
            select(Record)
            .where(
                Record.entity_id == entity_id,
                Record.owner_id == self.workspace_id,
                slug_condition,
            )
            .limit(limit)
            .offset(offset)
        )

        result = await self.session.exec(stmt)
        return list(result.all())

    # Helper Methods

    def get_active_fields_model(self, fields: list[FieldMetadata]) -> type[BaseModel]:
        """Generate Pydantic model for active fields only.

        Args:
            fields: Field definitions

        Returns:
            Dynamically created Pydantic model class
        """
        field_definitions: dict[str, Any] = {}

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
            "RecordModel",
            __config__=ConfigDict(extra="forbid"),
            **field_definitions,
        )

    async def get_entity_schema(self, entity_id: UUID) -> EntitySchemaResult:
        """Get entity schema information for UI/validation.

        Args:
            entity_id: Entity metadata ID

        Returns:
            Tuple of (Entity, list of active FieldMetadata)

        Raises:
            TracecatNotFoundError: If entity not found
        """
        # Get entity
        entity = await self.get_entity(entity_id)

        # Get active fields
        fields = await self.list_fields(entity_id, include_inactive=False)

        return EntitySchemaResult(entity=entity, fields=fields)
