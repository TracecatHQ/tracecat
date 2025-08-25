"""Service layer for custom entities with immutable field schemas."""

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, ValidationError, create_model
from pydantic import Field as PydanticField
from sqlalchemy import exc as sa_exc
from sqlalchemy.ext.asyncio import AsyncSession as SAAsyncSession
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.sql import ColumnElement
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.authz.controls import require_access_level
from tracecat.db.schemas import (
    Entity,
    FieldMetadata,
    Record,
    RecordRelationLink,
    RelationDefinition,
)
from tracecat.entities.common import (
    serialize_value,
)
from tracecat.entities.enums import RelationType
from tracecat.entities.models import (
    EntityCreate,
    EntitySchemaResult,
    FieldMetadataCreate,
    RelationDefinitionCreate,
    RelationDefinitionUpdate,
)
from tracecat.entities.query import EntityQueryBuilder
from tracecat.entities.types import (
    FieldType,
    get_python_type,
)
from tracecat.entities.validation import (
    EntityValidators,
    FieldValidators,
    NestedUpdateValidator,
    RecordValidators,
    RelationValidators,
    validate_default_value_type,
)
from tracecat.logger import logger
from tracecat.service import BaseWorkspaceService
from tracecat.types.auth import AccessLevel, Role
from tracecat.types.exceptions import TracecatNotFoundError
from tracecat.types.pagination import (
    BaseCursorPaginator,
    CursorPaginatedResponse,
    CursorPaginationParams,
)


class CustomEntitiesService(BaseWorkspaceService):
    """Service for custom entities with immutable field schemas."""

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

        # Deactivate entity and cascade to its fields and relations
        entity.is_active = False

        # Deactivate all fields owned by this entity
        now = datetime.now(UTC)
        fm_entity_col = cast(ColumnElement[Any], FieldMetadata.entity_id)
        fm_is_active_col = cast(ColumnElement[bool], FieldMetadata.is_active)
        await SAAsyncSession.execute(
            self.session,
            sa.update(FieldMetadata)
            .where(fm_entity_col == entity_id, fm_is_active_col)
            .values(is_active=False, deactivated_at=now),
        )

        # Deactivate relations where this entity is source or target
        src_col = cast(ColumnElement[Any], RelationDefinition.source_entity_id)
        tgt_col = cast(ColumnElement[Any], RelationDefinition.target_entity_id)
        rd_is_active_col = cast(ColumnElement[bool], RelationDefinition.is_active)
        await SAAsyncSession.execute(
            self.session,
            sa.update(RelationDefinition)
            .where(
                sa.or_(src_col == entity_id, tgt_col == entity_id),
                rd_is_active_col,
            )
            .values(is_active=False, deactivated_at=now),
        )

        await self.session.commit()
        await self.session.refresh(entity)

        logger.info(
            f"Deactivated entity {entity.name} and cascaded to fields/relations"
        )
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

        # Reactivate entity and cascade to its fields and relations
        entity.is_active = True

        # Reactivate all fields owned by this entity
        fm_entity_col = cast(ColumnElement[Any], FieldMetadata.entity_id)
        fm_is_active_col = cast(ColumnElement[bool], FieldMetadata.is_active)
        await SAAsyncSession.execute(
            self.session,
            sa.update(FieldMetadata)
            .where(fm_entity_col == entity_id, ~fm_is_active_col)
            .values(is_active=True, deactivated_at=None),
        )

        # Reactivate relations where this entity is source or target
        src_col = cast(ColumnElement[Any], RelationDefinition.source_entity_id)
        tgt_col = cast(ColumnElement[Any], RelationDefinition.target_entity_id)
        rd_is_active_col = cast(ColumnElement[bool], RelationDefinition.is_active)
        await SAAsyncSession.execute(
            self.session,
            sa.update(RelationDefinition)
            .where(
                sa.or_(src_col == entity_id, tgt_col == entity_id),
                ~rd_is_active_col,
            )
            .values(is_active=True, deactivated_at=None),
        )

        await self.session.commit()
        await self.session.refresh(entity)

        logger.info(
            f"Reactivated entity {entity.name} and cascaded to fields/relations"
        )
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

        # Delete relations owned or targeted by this entity and their links
        src_col = cast(ColumnElement[Any], RelationDefinition.source_entity_id)
        tgt_col = cast(ColumnElement[Any], RelationDefinition.target_entity_id)
        stmt_rel_ids = select(RelationDefinition.id).where(
            sa.or_(src_col == entity_id, tgt_col == entity_id)
        )
        rel_ids_res = await self.session.exec(stmt_rel_ids)
        rel_ids = list(rel_ids_res.all())
        if rel_ids:
            rel_id_col = cast(
                ColumnElement[Any], RecordRelationLink.relation_definition_id
            )
            await SAAsyncSession.execute(
                self.session,
                sa.delete(RecordRelationLink).where(rel_id_col.in_(rel_ids)),
            )
            await SAAsyncSession.execute(
                self.session,
                sa.delete(RelationDefinition).where(
                    cast(ColumnElement[Any], RelationDefinition.id).in_(rel_ids)
                ),
            )

        # Bulk delete all records of this entity for this workspace
        rec_entity_col = cast(ColumnElement[Any], Record.entity_id)
        rec_owner_col = cast(ColumnElement[Any], Record.owner_id)
        await SAAsyncSession.execute(
            self.session,
            sa.delete(Record).where(
                rec_entity_col == entity_id, rec_owner_col == self.workspace_id
            ),
        )

        # Bulk delete all fields of this entity
        fm_entity_col = cast(ColumnElement[Any], FieldMetadata.entity_id)
        await SAAsyncSession.execute(
            self.session, sa.delete(FieldMetadata).where(fm_entity_col == entity_id)
        )

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
        # Reject writes to inactive entities
        await self.entity_validators.validate_entity_active(entity_id)
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
        # Reject writes to inactive entities
        await self.entity_validators.validate_entity_active(field.entity_id)

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

        # Deactivate this field
        now = datetime.now(UTC)
        field.is_active = False
        field.deactivated_at = now

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

        # Reactivate this field
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
            - Deletes the field metadata
        """
        field = await self.get_field(field_id)

        # No relation cleanup is needed; relations are managed via RelationDefinition

        # Remove field key from all records' JSONB field_data in one update
        field_data_col = cast(ColumnElement[Any], Record.field_data)
        rec_entity_col2 = cast(ColumnElement[Any], Record.entity_id)
        rec_owner_col2 = cast(ColumnElement[Any], Record.owner_id)
        await SAAsyncSession.execute(
            self.session,
            sa.update(Record)
            .where(
                rec_entity_col2 == field.entity_id,
                rec_owner_col2 == self.workspace_id,
            )
            .values(field_data=field_data_col.op("-")(field.field_key)),
        )

        # Delete the field metadata itself
        await self.session.delete(field)

        await self.session.commit()
        logger.info(f"Permanently deleted field {field.field_key}")

    # Relation Definition Operations

    @require_access_level(AccessLevel.ADMIN)
    async def create_relation(
        self, entity_id: UUID, data: RelationDefinitionCreate
    ) -> RelationDefinition:
        # Validate source entity active
        await self.entity_validators.validate_entity_active(entity_id)
        # Validate target entity active
        await self.entity_validators.validate_entity_active(data.target_entity_id)

        # Validate against workspace relation policies
        await self.relation_validators.validate_relation_creation(
            source_entity_id=entity_id,
            target_entity_id=data.target_entity_id,
            relation_type=data.relation_type.value,
        )

        # Check uniqueness of source_key within source entity
        stmt = select(RelationDefinition).where(
            RelationDefinition.owner_id == self.workspace_id,
            RelationDefinition.source_entity_id == entity_id,
            RelationDefinition.source_key == data.source_key,
        )
        res = await self.session.exec(stmt)
        if res.first() is not None:
            raise ValueError(
                f"Relation key '{data.source_key}' already exists on this entity"
            )

        relation = RelationDefinition(
            owner_id=self.workspace_id,
            source_entity_id=entity_id,
            target_entity_id=data.target_entity_id,
            source_key=data.source_key,
            display_name=data.display_name,
            relation_type=data.relation_type.value,
            is_active=True,
        )
        self.session.add(relation)
        await self.session.commit()
        await self.session.refresh(relation)
        return relation

    async def get_relation(self, relation_id: UUID) -> RelationDefinition:
        """Get a single relation by ID.

        Args:
            relation_id: The relation ID

        Returns:
            The relation definition

        Raises:
            TracecatNotFoundError: If relation not found
        """
        stmt = select(RelationDefinition).where(
            RelationDefinition.id == relation_id,
            RelationDefinition.owner_id == self.workspace_id,
        )
        result = await self.session.exec(stmt)
        relation = result.first()
        if not relation:
            raise TracecatNotFoundError(f"Relation {relation_id} not found")
        return relation

    async def list_relations(
        self, entity_id: UUID, include_inactive: bool = False
    ) -> list[RelationDefinition]:
        stmt = select(RelationDefinition).where(
            RelationDefinition.owner_id == self.workspace_id,
            RelationDefinition.source_entity_id == entity_id,
        )
        if not include_inactive:
            stmt = stmt.where(RelationDefinition.is_active)
        res = await self.session.exec(stmt)
        return list(res.all())

    async def list_all_relations(
        self,
        *,
        source_entity_id: UUID | None = None,
        target_entity_id: UUID | None = None,
        include_inactive: bool = False,
    ) -> list[RelationDefinition]:
        """List relation definitions for the workspace with optional filters."""
        stmt = select(RelationDefinition).where(
            RelationDefinition.owner_id == self.workspace_id,
        )
        if source_entity_id is not None:
            stmt = stmt.where(RelationDefinition.source_entity_id == source_entity_id)
        if target_entity_id is not None:
            stmt = stmt.where(RelationDefinition.target_entity_id == target_entity_id)
        if not include_inactive:
            stmt = stmt.where(RelationDefinition.is_active)
        res = await self.session.exec(stmt)
        return list(res.all())

    async def list_all_fields(
        self,
        *,
        entity_id: UUID | None = None,
        include_inactive: bool = False,
    ) -> list[FieldMetadata]:
        """List field metadata across the workspace with optional filters.

        Filters by workspace ownership via a join to Entity.
        """
        stmt = (
            select(FieldMetadata)
            .join(
                Entity,
                cast(ColumnElement[Any], FieldMetadata.entity_id)
                == cast(ColumnElement[Any], Entity.id),
            )
            .where(Entity.owner_id == self.workspace_id)
        )
        if entity_id is not None:
            stmt = stmt.where(FieldMetadata.entity_id == entity_id)
        if not include_inactive:
            stmt = stmt.where(FieldMetadata.is_active)
        res = await self.session.exec(stmt)
        return list(res.all())

    @require_access_level(AccessLevel.ADMIN)
    async def update_relation(
        self, relation_id: UUID, params: RelationDefinitionUpdate
    ) -> RelationDefinition:
        stmt = select(RelationDefinition).where(
            RelationDefinition.id == relation_id,
            RelationDefinition.owner_id == self.workspace_id,
        )
        res = await self.session.exec(stmt)
        relation = res.first()
        if relation is None:
            raise TracecatNotFoundError(f"Relation {relation_id} not found")

        if params.source_key is not None and params.source_key != relation.source_key:
            # Enforce uniqueness
            dup_stmt = select(RelationDefinition).where(
                RelationDefinition.owner_id == self.workspace_id,
                RelationDefinition.source_entity_id == relation.source_entity_id,
                RelationDefinition.source_key == params.source_key,
                RelationDefinition.id != relation.id,
            )
            dup = await self.session.exec(dup_stmt)
            if dup.first() is not None:
                raise ValueError(
                    f"Relation key '{params.source_key}' already exists on this entity"
                )
            relation.source_key = params.source_key
        if params.display_name is not None:
            relation.display_name = params.display_name

        await self.session.commit()
        await self.session.refresh(relation)
        return relation

    @require_access_level(AccessLevel.ADMIN)
    async def deactivate_relation(self, relation_id: UUID) -> RelationDefinition:
        stmt = select(RelationDefinition).where(
            RelationDefinition.id == relation_id,
            RelationDefinition.owner_id == self.workspace_id,
        )
        res = await self.session.exec(stmt)
        relation = res.first()
        if relation is None:
            raise TracecatNotFoundError(f"Relation {relation_id} not found")
        if not relation.is_active:
            raise ValueError("Relation is already inactive")
        relation.is_active = False
        relation.deactivated_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(relation)
        return relation

    @require_access_level(AccessLevel.ADMIN)
    async def reactivate_relation(self, relation_id: UUID) -> RelationDefinition:
        stmt = select(RelationDefinition).where(
            RelationDefinition.id == relation_id,
            RelationDefinition.owner_id == self.workspace_id,
        )
        res = await self.session.exec(stmt)
        relation = res.first()
        if relation is None:
            raise TracecatNotFoundError(f"Relation {relation_id} not found")
        if relation.is_active:
            raise ValueError("Relation is already active")
        relation.is_active = True
        relation.deactivated_at = None
        await self.session.commit()
        await self.session.refresh(relation)
        return relation

    @require_access_level(AccessLevel.ADMIN)
    async def delete_relation(self, relation_id: UUID) -> None:
        """Permanently delete a relation and all its links.

        This is a hard delete that:
        - Deletes all RecordRelationLink entries for this relation
        - Deletes the RelationDefinition itself

        Args:
            relation_id: The ID of the relation to delete

        Raises:
            TracecatNotFoundError: If the relation doesn't exist
        """
        # Verify relation exists and belongs to this workspace
        stmt = select(RelationDefinition).where(
            RelationDefinition.id == relation_id,
            RelationDefinition.owner_id == self.workspace_id,
        )
        res = await self.session.exec(stmt)
        relation = res.first()
        if relation is None:
            raise TracecatNotFoundError(f"Relation {relation_id} not found")

        # Delete all relation links for this relation
        # This cascades automatically due to FK constraints, but explicit for clarity
        rel_id_col = cast(ColumnElement[Any], RecordRelationLink.relation_definition_id)
        await SAAsyncSession.execute(
            self.session, sa.delete(RecordRelationLink).where(rel_id_col == relation_id)
        )

        # Delete the relation definition
        await self.session.delete(relation)
        await self.session.commit()

    # Relation handling helpers

    # Note: record deletions rely on FK CASCADE to remove relation links.

    # Data Operations

    async def create_record(
        self, entity_id: UUID, data: dict[str, Any], depth: int = 0
    ) -> Record:
        """Create a new entity record with nested relation support.

        Args:
            entity_id: Entity to create record for
            data: Record data including nested relations
            depth: Current nesting depth for relation creation

        Returns:
            Created record

        Raises:
            ValueError: If depth limit exceeded or validation fails
        """
        # Check depth limit to prevent excessive nesting
        MAX_RELATION_CREATE_DEPTH = 2
        if depth > MAX_RELATION_CREATE_DEPTH:
            raise ValueError(
                f"Maximum nested relation depth ({MAX_RELATION_CREATE_DEPTH}) exceeded"
            )

        # Reject writes to inactive entities
        await self.entity_validators.validate_entity_active(entity_id)

        active_fields = await self.list_fields(entity_id, include_inactive=False)
        # Load active relations for this entity (as source)
        active_relations = await self.list_relations(entity_id, include_inactive=False)
        relation_map = {r.source_key: r for r in active_relations}

        relation_data, data_without_relations = self._extract_relation_data_by_rel(
            data, relation_map
        )

        data_with_defaults = self._apply_default_values(
            data_without_relations, active_fields
        )

        all_data_to_validate = {**data_with_defaults, **relation_data}
        validated_data = await self.record_validators.validate_record_data(
            all_data_to_validate, active_fields, relation_map
        )

        field_map = {f.field_key: f for f in active_fields}
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
            await self._create_relation_links_new(
                entity_id=entity_id,
                record=record,
                relation_data=relation_data,
                relation_defs=relation_map,
                depth=depth,
            )
            try:
                await self.session.commit()
            except sa_exc.IntegrityError as e:  # Concurrency-safe error mapping
                msg = str(getattr(e, "orig", e))
                if "uq_record_relation_source_single" in msg:
                    raise ValueError("Relation already set for this record") from e
                if "uq_record_relation_target_single" in msg:
                    raise ValueError("Target already linked for this relation") from e
                if "uq_record_relation_link_triple" in msg:
                    raise ValueError("Duplicate relation link") from e
                raise

        return record

    # Internal helpers for record creation
    def _extract_relation_data_by_rel(
        self, data: dict[str, Any], relation_defs: dict[str, RelationDefinition]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        relation_data: dict[str, Any] = {}
        data_without_relations = dict(data)
        for key in list(data_without_relations.keys()):
            if key in relation_defs:
                relation_data[key] = data_without_relations.pop(key)
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
            if field is None:
                continue
            serialized_data[key] = serialize_value(value, FieldType(field.field_type))
        return serialized_data

    async def _create_relation_links_new(
        self,
        *,
        entity_id: UUID,
        record: Record,
        relation_data: dict[str, Any],
        relation_defs: dict[str, RelationDefinition],
        depth: int = 0,
    ) -> None:
        for source_key, value in relation_data.items():
            if value is None:
                continue

            rel_def = relation_defs[source_key]
            target_entity_id = rel_def.target_entity_id

            # Handle inline record creation for dicts/UUIDs
            target_ids = await self._process_relation_value_new(
                rel_def, value, target_entity_id, depth
            )
            if not target_ids:
                continue

            # Compute DB-enforced cardinality flags from relation type
            rt = RelationType(rel_def.relation_type)
            if rt == RelationType.ONE_TO_ONE:
                source_limit_one = True
                target_limit_one = True
            elif rt == RelationType.MANY_TO_ONE:
                source_limit_one = True
                target_limit_one = False
            else:
                # ONE_TO_MANY or MANY_TO_MANY
                source_limit_one = False
                target_limit_one = False

            for target_id in target_ids:
                # Pre-check for uniqueness to provide clearer errors before DB constraint
                if source_limit_one:
                    existing_src_stmt = select(RecordRelationLink).where(
                        RecordRelationLink.source_record_id == record.id,
                        RecordRelationLink.relation_definition_id == rel_def.id,
                    )
                    existing_src_res = await self.session.exec(existing_src_stmt)
                    if existing_src_res.first() is not None:
                        raise ValueError(
                            f"Relation '{source_key}' is already set for this record"
                        )

                if target_limit_one:
                    existing_tgt_stmt = select(RecordRelationLink).where(
                        RecordRelationLink.target_record_id == target_id,
                        RecordRelationLink.relation_definition_id == rel_def.id,
                    )
                    existing_tgt_res = await self.session.exec(existing_tgt_stmt)
                    if existing_tgt_res.first() is not None:
                        raise ValueError(
                            f"Target record is already linked via '{source_key}'"
                        )

                link = RecordRelationLink(
                    owner_id=self.workspace_id,
                    source_entity_id=entity_id,
                    relation_definition_id=rel_def.id,
                    source_record_id=record.id,
                    target_entity_id=target_entity_id,  # Already a UUID
                    target_record_id=target_id,
                    source_limit_one=source_limit_one,
                    target_limit_one=target_limit_one,
                )
                self.session.add(link)

    async def _process_relation_value_new(
        self,
        rel_def: RelationDefinition,
        value: Any,
        target_entity_id: UUID,
        depth: int = 0,
    ) -> list[UUID]:
        """Process relation value, creating records for dicts or validating UUIDs.

        Args:
            rel_def: Relation definition
            value: Value to process (UUID, string, dict, or list)
            target_entity_id: Target entity ID for the relation
            depth: Current nesting depth

        Returns:
            List of target record IDs
        """
        rt = RelationType(rel_def.relation_type)
        if rt in (RelationType.ONE_TO_ONE, RelationType.MANY_TO_ONE):
            if value is None:
                return []

            # Handle dict for inline creation
            if isinstance(value, dict):
                # Create new record for the target entity with incremented depth
                created_record = await self.create_record(
                    target_entity_id, value, depth + 1
                )
                return [created_record.id]

            # Handle UUID or string
            target_id = UUID(value) if isinstance(value, str) else value
            await self._validate_target_records_exist([target_id], target_entity_id)
            return [target_id]

        # ONE_TO_MANY or MANY_TO_MANY
        ids: list[UUID] = []
        for item in value:
            if isinstance(item, dict):
                # Create new record for the target entity with incremented depth
                created_record = await self.create_record(
                    target_entity_id, item, depth + 1
                )
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

    def _normalize_target_ids_new(
        self, rel_def: RelationDefinition, value: Any
    ) -> list[UUID]:
        rt = RelationType(rel_def.relation_type)
        if rt in (RelationType.ONE_TO_ONE, RelationType.MANY_TO_ONE):
            if value is None:
                return []
            return [UUID(value) if isinstance(value, str) else value]
        # ONE_TO_MANY / MANY_TO_MANY
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
            Record.id == record_id,
            Record.owner_id == self.workspace_id,
            col(Record.deleted_at).is_(None),
        )
        result = await self.session.exec(stmt)
        record = result.first()

        if not record:
            raise TracecatNotFoundError(f"Record {record_id} not found")

        return record

    async def _create_relation_for_update(
        self,
        source_record: Record,
        rel_def: RelationDefinition,
        relation_data: dict[str, Any],
    ) -> None:
        """Create a new related record and link during update.

        Args:
            source_record: The source record being updated
            field: The relation field metadata
            relation_data: Data for creating the related record
        """
        target_entity_id = rel_def.target_entity_id

        # Create the related record
        target_record = await self.create_record(target_entity_id, relation_data)

        # Compute DB-enforced cardinality flags based on field type
        rt = RelationType(rel_def.relation_type)
        if rt == RelationType.ONE_TO_ONE:
            source_limit_one = True
            target_limit_one = True
        elif rt == RelationType.MANY_TO_ONE:
            source_limit_one = True
            target_limit_one = False
        else:
            source_limit_one = False
            target_limit_one = False

        # Pre-checks to surface clearer errors before DB constraint violations
        if source_limit_one:
            existing_src_stmt = select(RecordRelationLink).where(
                RecordRelationLink.source_record_id == source_record.id,
                RecordRelationLink.relation_definition_id == rel_def.id,
            )
            existing_src_res = await self.session.exec(existing_src_stmt)
            if existing_src_res.first() is not None:
                raise ValueError(
                    f"Relation '{rel_def.source_key}' is already set for this record"
                )

        if target_limit_one:
            existing_tgt_stmt = select(RecordRelationLink).where(
                RecordRelationLink.target_record_id == target_record.id,
                RecordRelationLink.relation_definition_id == rel_def.id,
            )
            existing_tgt_res = await self.session.exec(existing_tgt_stmt)
            if existing_tgt_res.first() is not None:
                raise ValueError(
                    f"Target record is already linked via '{rel_def.source_key}'"
                )

        # Create the relation link
        link = RecordRelationLink(
            owner_id=self.workspace_id,
            source_entity_id=source_record.entity_id,
            relation_definition_id=rel_def.id,
            source_record_id=source_record.id,
            target_entity_id=target_entity_id,
            target_record_id=target_record.id,
            source_limit_one=source_limit_one,
            target_limit_one=target_limit_one,
        )
        self.session.add(link)

    async def update_record(self, record_id: UUID, updates: dict[str, Any]) -> Record:
        """Update entity record with support for nested relation updates.

        Args:
            record_id: Record to update
            updates: Field updates (can contain nested updates for relations)

        Returns:
            Updated record

        Note:
            - Relations links remain immutable
            - Nested updates modify target entity's fields
            - All updates execute in a single transaction
            - Circular references are detected and handled
            - Null relations with dict values create new related records
        """
        # First, handle creation/update of relations according to update semantics
        source_record = await self.get_record(record_id)
        # Reject writes to inactive entities (source)
        await self.entity_validators.validate_entity_active(source_record.entity_id)
        active_fields = await self.list_fields(
            source_record.entity_id, include_inactive=False
        )

        # Work on a copy of updates and progressively consume relation keys
        updates_copy = updates.copy()

        # Load active relations for this source entity
        active_relations = await self.list_relations(
            source_record.entity_id, include_inactive=False
        )
        relation_map = {r.source_key: r for r in active_relations}

        # Helper: create a link to an existing target id, enforcing cardinality
        async def _link_existing_target(
            src_record: Record, rel_def: RelationDefinition, target_id: UUID
        ) -> None:
            rt = RelationType(rel_def.relation_type)
            if rt == RelationType.ONE_TO_ONE:
                source_limit_one = True
                target_limit_one = True
            elif rt == RelationType.MANY_TO_ONE:
                source_limit_one = True
                target_limit_one = False
            else:
                source_limit_one = False
                target_limit_one = False

            if source_limit_one:
                existing_src_stmt = select(RecordRelationLink).where(
                    RecordRelationLink.source_record_id == src_record.id,
                    RecordRelationLink.relation_definition_id == rel_def.id,
                )
                if (await self.session.exec(existing_src_stmt)).first() is not None:
                    raise ValueError(
                        f"Relation '{rel_def.source_key}' is already set for this record"
                    )

            if target_limit_one:
                existing_tgt_stmt = select(RecordRelationLink).where(
                    RecordRelationLink.target_record_id == target_id,
                    RecordRelationLink.relation_definition_id == rel_def.id,
                )
                if (await self.session.exec(existing_tgt_stmt)).first() is not None:
                    raise ValueError(
                        f"Target record is already linked via '{rel_def.source_key}'"
                    )

            link = RecordRelationLink(
                owner_id=self.workspace_id,
                source_entity_id=src_record.entity_id,
                relation_definition_id=rel_def.id,
                source_record_id=src_record.id,
                target_entity_id=rel_def.target_entity_id,
                target_record_id=target_id,
                source_limit_one=source_limit_one,
                target_limit_one=target_limit_one,
            )
            self.session.add(link)

        # Helper: remove specific links for a relation
        async def _remove_relation_links(
            src_record_id: UUID, rel_def: RelationDefinition, target_ids: list[UUID]
        ) -> None:
            if not target_ids:
                return
            stmt = select(RecordRelationLink).where(
                RecordRelationLink.source_record_id == src_record_id,
                RecordRelationLink.relation_definition_id == rel_def.id,
                cast(Any, RecordRelationLink.target_record_id).in_(target_ids),
            )
            res = await self.session.exec(stmt)
            for link in res.all():
                await self.session.delete(link)

        # Helper: clear all links for a relation
        async def _clear_relation_links(
            src_record_id: UUID, rel_def: RelationDefinition
        ) -> None:
            stmt = select(RecordRelationLink).where(
                RecordRelationLink.source_record_id == src_record_id,
                RecordRelationLink.relation_definition_id == rel_def.id,
            )
            res = await self.session.exec(stmt)
            for link in res.all():
                await self.session.delete(link)

        # 1) Pre-handle inline nested creation for O2O/M2O with dict value (if no link)
        for source_key, rel_def in relation_map.items():
            rt = RelationType(rel_def.relation_type)
            if rt in (RelationType.ONE_TO_ONE, RelationType.MANY_TO_ONE):
                rel_value = updates.get(source_key)
                if isinstance(rel_value, dict) and rel_value:
                    stmt = select(RecordRelationLink).where(
                        RecordRelationLink.source_record_id == record_id,
                        RecordRelationLink.relation_definition_id == rel_def.id,
                    )
                    if (await self.session.exec(stmt)).first() is None:
                        await self._create_relation_for_update(
                            source_record, rel_def, rel_value
                        )
                        updates_copy.pop(source_key, None)

        # 2) Operators: __add, __remove, __set, __clear
        for source_key, rel_def in relation_map.items():
            add_key = f"{source_key}__add"
            remove_key = f"{source_key}__remove"
            set_key = f"{source_key}__set"
            clear_key = f"{source_key}__clear"

            # Clear
            if clear_key in updates_copy:
                await _clear_relation_links(source_record.id, rel_def)
                updates_copy.pop(clear_key, None)

            # Add
            if add_key in updates_copy:
                value = updates_copy.pop(add_key)
                rt = RelationType(rel_def.relation_type)
                if rt in (RelationType.ONE_TO_MANY, RelationType.MANY_TO_MANY):
                    # Use create path helper for arrays
                    await self._create_relation_links_new(
                        entity_id=source_record.entity_id,
                        record=source_record,
                        relation_data={source_key: value},
                        relation_defs={source_key: rel_def},
                        depth=0,
                    )
                else:
                    # Single-side add: accept dict/UUID, like set-if-empty
                    if isinstance(value, dict) and value:
                        await self._create_relation_for_update(
                            source_record, rel_def, value
                        )
                    else:
                        # UUID or str
                        if isinstance(value, str):
                            target_id = UUID(value)
                        elif isinstance(value, UUID):
                            target_id = value
                        else:
                            raise ValueError(f"Invalid target ID type: {type(value)}")
                        await self._validate_target_records_exist(
                            [target_id], rel_def.target_entity_id
                        )
                        await _link_existing_target(source_record, rel_def, target_id)

            # Remove
            if remove_key in updates_copy:
                value = updates_copy.pop(remove_key)
                # Normalize list of ids
                if isinstance(value, list):
                    ids = [UUID(v) if isinstance(v, str) else v for v in value]
                else:
                    ids = [UUID(value) if isinstance(value, str) else value]
                await _remove_relation_links(source_record.id, rel_def, ids)

            # Set
            if set_key in updates_copy:
                value = updates_copy.pop(set_key)
                rt = RelationType(rel_def.relation_type)
                # Clear existing links first
                await _clear_relation_links(source_record.id, rel_def)
                if rt in (RelationType.ONE_TO_ONE, RelationType.MANY_TO_ONE):
                    # Expect single dict/UUID (or None)
                    if value is None:
                        continue
                    if isinstance(value, dict) and value:
                        await self._create_relation_for_update(
                            source_record, rel_def, value
                        )
                    else:
                        if isinstance(value, str):
                            target_id = UUID(value)
                        elif isinstance(value, UUID):
                            target_id = value
                        else:
                            raise ValueError(f"Invalid target ID type: {type(value)}")
                        await self._validate_target_records_exist(
                            [target_id], rel_def.target_entity_id
                        )
                        await _link_existing_target(source_record, rel_def, target_id)
                else:
                    # List relations: accept list of dict/UUID
                    await self._create_relation_links_new(
                        entity_id=source_record.entity_id,
                        record=source_record,
                        relation_data={source_key: value},
                        relation_defs={source_key: rel_def},
                        depth=0,
                    )

        # 3) Additive arrays for list relations via plain key: treat as __add
        for source_key, rel_def in relation_map.items():
            rt = RelationType(rel_def.relation_type)
            if source_key in updates_copy and rt in (
                RelationType.ONE_TO_MANY,
                RelationType.MANY_TO_MANY,
            ):
                value = updates_copy.pop(source_key)
                await self._create_relation_links_new(
                    entity_id=source_record.entity_id,
                    record=source_record,
                    relation_data={source_key: value},
                    relation_defs={source_key: rel_def},
                    depth=0,
                )

        # Create validator with query builder for efficiency
        validator = NestedUpdateValidator(
            self.session, self.workspace_id, self.query_builder
        )

        # Build and validate complete update plan upfront (with remaining updates)
        update_plan = await validator.validate_and_plan_updates(record_id, updates_copy)

        # Ensure all entities in the plan are active before applying updates
        for step in update_plan.steps:
            await self.entity_validators.validate_entity_active(step.entity_id)

        # Execute all updates in a single transaction with savepoint
        async with self.session.begin_nested():
            for step in update_plan.steps:
                # Load the record to update
                record = await self.get_record(step.record_id)

                # Get active fields for this entity
                active_fields = await self.list_fields(
                    step.entity_id, include_inactive=False
                )
                field_map = {f.field_key: f for f in active_fields}

                # Validate the field updates for this step
                if step.field_updates:
                    validated_updates = (
                        await self.record_validators.validate_record_data(
                            step.field_updates, active_fields
                        )
                    )

                    # Apply updates to field_data
                    for key, value in validated_updates.items():
                        field = field_map.get(key)
                        if field:
                            record.field_data[key] = serialize_value(
                                value, FieldType(field.field_type)
                            )

                    # Mark field_data as modified for SQLAlchemy
                    flag_modified(record, "field_data")

        # Single commit for all changes (with constraint error mapping)
        try:
            await self.session.commit()
        except sa_exc.IntegrityError as e:
            msg = str(getattr(e, "orig", e))
            if "uq_record_relation_source_single" in msg:
                raise ValueError("Relation already set for this record") from e
            if "uq_record_relation_target_single" in msg:
                raise ValueError("Target already linked for this relation") from e
            if "uq_record_relation_link_triple" in msg:
                raise ValueError("Duplicate relation link") from e
            raise

        # Refresh and return the main record
        main_record = await self.get_record(record_id)
        await self.session.refresh(main_record)
        return main_record

    async def delete_record(self, record_id: UUID) -> None:
        """Delete entity record.

        Args:
            record_id: Record to delete
        """
        stmt = select(Record).where(
            Record.id == record_id, Record.owner_id == self.workspace_id
        )
        result = await self.session.exec(stmt)
        record = result.first()
        if not record:
            raise TracecatNotFoundError(f"Record {record_id} not found")
        # Reject writes to inactive entities
        await self.entity_validators.validate_entity_active(record.entity_id)
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
            col(Record.deleted_at).is_(None),
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
            col(Record.deleted_at).is_(None),
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

    # Global records lifecycle and listing

    async def list_all_records(
        self,
        *,
        entity_id: UUID | None = None,
        include_deleted: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Record]:
        """List records globally across entities.

        Args:
            entity_id: Optional filter by entity ID
            include_deleted: Include archived (soft-deleted) records if True
            limit: Max records
            offset: Offset for pagination
        """
        stmt = select(Record).where(Record.owner_id == self.workspace_id)
        if entity_id is not None:
            stmt = stmt.where(Record.entity_id == entity_id)
        if not include_deleted:
            stmt = stmt.where(col(Record.deleted_at).is_(None))
        stmt = stmt.limit(limit).offset(offset)
        result = await self.session.exec(stmt)
        return list(result.all())

    async def list_all_records_paginated(
        self,
        params: CursorPaginationParams,
        *,
        entity_id: UUID | None = None,
        include_deleted: bool = False,
    ) -> CursorPaginatedResponse[Record]:
        """List records globally with cursor-based pagination.

        Orders by created_at desc, id desc. Supports optional entity filter and
        include_deleted to include archived records.
        """
        paginator = BaseCursorPaginator(self.session)

        # Estimated total count for UX (may be None)
        total_estimate = await paginator.get_table_row_estimate("record")

        # Base query
        stmt = (
            select(Record)
            .where(Record.owner_id == self.workspace_id)
            .order_by(col(Record.created_at).desc(), col(Record.id).desc())
        )

        if entity_id is not None:
            stmt = stmt.where(Record.entity_id == entity_id)
        if not include_deleted:
            stmt = stmt.where(col(Record.deleted_at).is_(None))

        # Apply cursor filtering
        if params.cursor:
            cursor_data = paginator.decode_cursor(params.cursor)
            cursor_time = cursor_data.created_at
            cursor_id = UUID(cursor_data.id)

            if params.reverse:
                stmt = stmt.where(
                    sa.or_(
                        col(Record.created_at) > cursor_time,
                        sa.and_(
                            col(Record.created_at) == cursor_time,
                            col(Record.id) > cursor_id,
                        ),
                    )
                ).order_by(col(Record.created_at).asc(), col(Record.id).asc())
            else:
                stmt = stmt.where(
                    sa.or_(
                        col(Record.created_at) < cursor_time,
                        sa.and_(
                            col(Record.created_at) == cursor_time,
                            col(Record.id) < cursor_id,
                        ),
                    )
                )

        # Fetch limit + 1 to determine if there is a next page
        stmt = stmt.limit(params.limit + 1)
        result = await self.session.exec(stmt)
        all_records = list(result.all())

        # Determine paging
        has_more = len(all_records) > params.limit
        records = all_records[: params.limit] if has_more else all_records

        next_cursor: str | None = None
        prev_cursor: str | None = None
        has_previous = params.cursor is not None

        if has_more and records:
            last_record = records[-1]
            next_cursor = paginator.encode_cursor(
                last_record.created_at, last_record.id
            )

        if params.cursor and records:
            first_record = records[0]
            if params.reverse:
                # For reverse, first item becomes next cursor
                next_cursor = paginator.encode_cursor(
                    first_record.created_at, first_record.id
                )
            else:
                prev_cursor = paginator.encode_cursor(
                    first_record.created_at, first_record.id
                )

        return CursorPaginatedResponse(
            items=records,
            next_cursor=next_cursor,
            prev_cursor=prev_cursor,
            has_more=has_more,
            has_previous=has_previous,
            total_estimate=total_estimate,
        )

    async def archive_record(self, record_id: UUID) -> Record:
        """Soft delete (archive) a record by setting deleted_at.

        Returns the archived record.
        """
        # Fetch ignoring deleted filter
        stmt = select(Record).where(
            Record.id == record_id, Record.owner_id == self.workspace_id
        )
        result = await self.session.exec(stmt)
        record = result.first()
        if not record:
            raise TracecatNotFoundError(f"Record {record_id} not found")

        # Reject writes to inactive entities
        await self.entity_validators.validate_entity_active(record.entity_id)

        if record.deleted_at is None:
            record.deleted_at = datetime.now(UTC)
            await self.session.commit()
            await self.session.refresh(record)
        return record

    async def restore_record(self, record_id: UUID) -> Record:
        """Restore a soft-deleted record (clear deleted_at)."""
        stmt = select(Record).where(
            Record.id == record_id, Record.owner_id == self.workspace_id
        )
        result = await self.session.exec(stmt)
        record = result.first()
        if not record:
            raise TracecatNotFoundError(f"Record {record_id} not found")

        # Reject writes to inactive entities
        await self.entity_validators.validate_entity_active(record.entity_id)

        if record.deleted_at is not None:
            record.deleted_at = None
            await self.session.commit()
            await self.session.refresh(record)
        return record

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

            # Skip relation fields that don't store values in field_data
            if py_type is None:
                continue

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

        # Get active fields and relations
        fields = await self.list_fields(entity_id, include_inactive=False)
        relations = await self.list_relations(entity_id, include_inactive=False)

        return EntitySchemaResult(entity=entity, fields=fields, relations=relations)
