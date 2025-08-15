"""API router for custom entities."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from tracecat.auth.credentials import RoleACL
from tracecat.db.dependencies import AsyncDBSession
from tracecat.entities.models import (
    BelongsToRelationUpdate,
    EntityCreate,
    EntityRead,
    EntitySchemaField,
    EntitySchemaInfo,
    EntitySchemaResponse,
    EntityUpdate,
    FieldMetadataCreate,
    FieldMetadataRead,
    FieldMetadataUpdate,
    HasManyRelationUpdate,
    QueryRequest,
    QueryResponse,
    RecordCreate,
    RecordRead,
    RecordUpdate,
    RelationListRequest,
    RelationListResponse,
    RelationUpdateResponse,
)
from tracecat.entities.service import CustomEntitiesService
from tracecat.entities.types import FieldType
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatNotFoundError

router = APIRouter(prefix="/entities", tags=["entities"])

# Dependencies
WorkspaceUser = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
    ),
]


# Entity Type Management


@router.post("/types", response_model=EntityRead)
async def create_entity(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    params: EntityCreate,
) -> EntityRead:
    """Create a new entity."""
    service = CustomEntitiesService(session, role)
    try:
        entity = await service.create_entity(
            name=params.name,
            display_name=params.display_name,
            description=params.description,
            icon=params.icon,
        )
        return EntityRead.model_validate(entity, from_attributes=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/types", response_model=list[EntityRead])
async def list_entities(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    include_inactive: bool = Query(False, description="Include soft-deleted entities"),
) -> list[EntityRead]:
    """List all entities."""
    service = CustomEntitiesService(session, role)
    entities = await service.list_entities(include_inactive=include_inactive)
    return [EntityRead.model_validate(e, from_attributes=True) for e in entities]


@router.get("/types/{entity_id}", response_model=EntityRead)
async def get_entity(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: UUID,
) -> EntityRead:
    """Get entity by ID."""
    service = CustomEntitiesService(session, role)
    try:
        entity = await service.get_entity(entity_id)
        return EntityRead.model_validate(entity, from_attributes=True)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.patch("/types/{entity_id}", response_model=EntityRead)
async def update_entity(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: UUID,
    params: EntityUpdate,
) -> EntityRead:
    """Update entity display properties."""
    service = CustomEntitiesService(session, role)
    try:
        entity = await service.update_entity(
            entity_id=entity_id,
            display_name=params.display_name,
            description=params.description,
            icon=params.icon,
        )
        return EntityRead.model_validate(entity, from_attributes=True)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.delete("/types/{entity_id}", response_model=EntityRead)
async def deactivate_entity(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: UUID,
) -> EntityRead:
    """Soft delete entity."""
    service = CustomEntitiesService(session, role)
    try:
        entity = await service.deactivate_entity(entity_id)
        return EntityRead.model_validate(entity, from_attributes=True)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/types/{entity_id}/reactivate", response_model=EntityRead)
async def reactivate_entity(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: UUID,
) -> EntityRead:
    """Reactivate soft-deleted entity."""
    service = CustomEntitiesService(session, role)
    try:
        entity = await service.reactivate_entity(entity_id)
        return EntityRead.model_validate(entity, from_attributes=True)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete("/types/{entity_id}/hard", status_code=status.HTTP_204_NO_CONTENT)
async def delete_entity(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: UUID,
) -> None:
    """Permanently delete an entity and all associated data.

    Warning: This is a hard delete - all data will be permanently lost.
    This includes all records, fields, and relation links.
    """
    service = CustomEntitiesService(session, role)
    try:
        await service.delete_entity(entity_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


# Field Management


@router.post("/{entity_id}/fields", response_model=FieldMetadataRead)
async def create_field(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: UUID,
    params: FieldMetadataCreate,
) -> FieldMetadataRead:
    """Create a new field for an entity."""
    service = CustomEntitiesService(session, role)
    try:
        # Pass parameters directly - Pydantic handles None values properly
        field = await service.create_field(
            entity_id=entity_id,
            field_key=params.field_key,
            field_type=params.field_type,
            display_name=params.display_name,
            description=params.description,
            enum_options=params.enum_options,
            default_value=params.default_value,
        )
        return FieldMetadataRead.model_validate(field, from_attributes=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/{entity_id}/fields", response_model=list[FieldMetadataRead])
async def list_fields(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: UUID,
    include_inactive: bool = Query(False, description="Include soft-deleted fields"),
) -> list[FieldMetadataRead]:
    """List fields for an entity."""
    service = CustomEntitiesService(session, role)
    fields = await service.list_fields(entity_id, include_inactive=include_inactive)
    return [FieldMetadataRead.model_validate(f, from_attributes=True) for f in fields]


@router.get("/fields/{field_id}", response_model=FieldMetadataRead)
async def get_field(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    field_id: UUID,
) -> FieldMetadataRead:
    """Get field by ID."""
    service = CustomEntitiesService(session, role)
    try:
        field = await service.get_field(field_id)
        return FieldMetadataRead.model_validate(field, from_attributes=True)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.patch("/fields/{field_id}", response_model=FieldMetadataRead)
async def update_field(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    field_id: UUID,
    params: FieldMetadataUpdate,
) -> FieldMetadataRead:
    """Update field display properties (not schema)."""
    service = CustomEntitiesService(session, role)
    try:
        field = await service.update_field(
            field_id=field_id,
            display_name=params.display_name,
            description=params.description,
            enum_options=params.enum_options,
            default_value=params.default_value,
        )
        return FieldMetadataRead.model_validate(field, from_attributes=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/fields/{field_id}/deactivate", response_model=FieldMetadataRead)
async def deactivate_field(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    field_id: UUID,
) -> FieldMetadataRead:
    """Soft delete field (data preserved)."""
    service = CustomEntitiesService(session, role)
    try:
        field = await service.deactivate_field(field_id)
        return FieldMetadataRead.model_validate(field, from_attributes=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/fields/{field_id}/reactivate", response_model=FieldMetadataRead)
async def reactivate_field(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    field_id: UUID,
) -> FieldMetadataRead:
    """Reactivate soft-deleted field."""
    service = CustomEntitiesService(session, role)
    try:
        field = await service.reactivate_field(field_id)
        return FieldMetadataRead.model_validate(field, from_attributes=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.delete("/fields/{field_id}/hard", status_code=status.HTTP_204_NO_CONTENT)
async def delete_field(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    field_id: UUID,
) -> None:
    """Permanently delete a field and all associated data.

    Warning: This is a hard delete - all data will be permanently lost.
    """
    service = CustomEntitiesService(session, role)
    try:
        await service.delete_field(field_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


# Relation Field Endpoints


@router.post("/{entity_id}/fields/relation", response_model=FieldMetadataRead)
async def create_relation_field(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: UUID,
    params: FieldMetadataCreate,
) -> FieldMetadataRead:
    """Create a relation field.

    Requires relation_settings in the request body.
    """
    if not params.relation_settings:
        raise HTTPException(
            status_code=400, detail="relation_settings required for relation fields"
        )

    service = CustomEntitiesService(session, role)
    try:
        field = await service.create_relation_field(
            entity_id=entity_id,
            field_key=params.field_key,
            field_type=params.field_type,
            display_name=params.display_name,
            relation_settings=params.relation_settings,
            description=params.description,
        )
        return FieldMetadataRead.model_validate(field, from_attributes=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


# Data Operations


@router.post("/{entity_id}/records", response_model=RecordRead)
async def create_record(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: UUID,
    data: RecordCreate,
) -> RecordRead:
    """Create a new record."""
    service = CustomEntitiesService(session, role)
    try:
        # Extract dynamic fields from request
        record_data = data.model_dump(exclude_unset=True)

        record = await service.create_record(entity_id, record_data)
        return RecordRead.model_validate(record, from_attributes=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/records/{record_id}", response_model=RecordRead)
async def get_record(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    record_id: UUID,
) -> RecordRead:
    """Get record by ID."""
    service = CustomEntitiesService(session, role)
    try:
        record = await service.get_record(record_id)
        return RecordRead.model_validate(record, from_attributes=True)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.patch("/records/{record_id}", response_model=RecordRead)
async def update_record(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    record_id: UUID,
    updates: RecordUpdate,
) -> RecordRead:
    """Update record."""
    service = CustomEntitiesService(session, role)
    try:
        # Extract dynamic field updates
        update_data = updates.model_dump(exclude_unset=True)

        record = await service.update_record(record_id, update_data)
        return RecordRead.model_validate(record, from_attributes=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.delete("/records/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_record(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    record_id: UUID,
) -> None:
    """Delete record."""
    service = CustomEntitiesService(session, role)
    try:
        await service.delete_record(record_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/{entity_id}/query", response_model=QueryResponse)
async def query_records(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: UUID,
    query: QueryRequest,
) -> QueryResponse:
    """Query records with filters."""
    service = CustomEntitiesService(session, role)
    try:
        # Convert filter models to dicts
        filters = [f.model_dump() for f in query.filters]

        records = await service.query_records(
            entity_id=entity_id,
            filters=filters,
            limit=query.limit,
            offset=query.offset,
        )

        return QueryResponse(
            records=[
                RecordRead.model_validate(r, from_attributes=True) for r in records
            ],
            limit=query.limit,
            offset=query.offset,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


# Record Relation Endpoints


@router.put(
    "/records/{record_id}/relations/{field_key}",
    response_model=RelationUpdateResponse,
)
async def update_record_relation(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    record_id: UUID,
    field_key: str,
    value: BelongsToRelationUpdate | HasManyRelationUpdate,
) -> RelationUpdateResponse:
    """Update a relation field value.

    For belongs_to: Accept BelongsToRelationUpdate
    For has_many: Accept HasManyRelationUpdate
    """
    service = CustomEntitiesService(session, role)

    try:
        # Get the record and field metadata
        record = await service.get_record(record_id)

        # Get the field by key
        field = await service.get_field_by_key(record.entity_id, field_key)

        if not field:
            raise TracecatNotFoundError(f"Field '{field_key}' not found")

        # Check if it's a relation field
        if field.field_type not in (
            FieldType.RELATION_BELONGS_TO,
            FieldType.RELATION_HAS_MANY,
        ):
            raise ValueError(f"Field '{field_key}' is not a relation field")

        # Handle based on field type
        if field.field_type == FieldType.RELATION_BELONGS_TO:
            # Validate value is BelongsToRelationUpdate
            if not isinstance(value, BelongsToRelationUpdate):
                raise HTTPException(
                    status_code=400,
                    detail="Belongs-to relation expects BelongsToRelationUpdate",
                )

            await service.update_belongs_to_relation(
                source_record_id=record_id,
                field=field,
                target_record_id=value.target_id,
            )

            return RelationUpdateResponse(
                message="Relation updated",
                target_id=str(value.target_id) if value.target_id else None,
            )

        else:  # RELATION_HAS_MANY
            # Validate value is HasManyRelationUpdate
            if not isinstance(value, HasManyRelationUpdate):
                raise HTTPException(
                    status_code=400,
                    detail="Has-many relation expects HasManyRelationUpdate",
                )

            stats = await service.update_has_many_relation(
                source_record_id=record_id,
                field=field,
                operation=value,
            )

            return RelationUpdateResponse(
                message="Relation updated",
                stats=stats,
            )

    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get(
    "/records/{record_id}/relations/{field_key}", response_model=RelationListResponse
)
async def list_related_records(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    record_id: UUID,
    field_key: str,
    request: RelationListRequest = RelationListRequest(),
) -> RelationListResponse:
    """List related records with pagination.

    REQUIRED: Pagination parameters (page, page_size)
    Max page_size: 100
    """
    service = CustomEntitiesService(session, role)

    try:
        # Get the record and field metadata
        record = await service.get_record(record_id)

        # Get the field by key
        field = await service.get_field_by_key(record.entity_id, field_key)

        if not field:
            raise TracecatNotFoundError(f"Field '{field_key}' not found")

        # Check if it's a relation field
        if field.field_type not in (
            FieldType.RELATION_BELONGS_TO,
            FieldType.RELATION_HAS_MANY,
        ):
            raise ValueError(f"Field '{field_key}' is not a relation field")

        # Query related records
        filters = [f.model_dump() for f in request.filters] if request.filters else None

        records, total = await service.query_builder.has_related(
            source_record_id=record_id,
            field_id=field.id,
            target_filters=filters,
            page=request.page,
            page_size=request.page_size,
        )

        has_next = (request.page * request.page_size) < total

        return RelationListResponse(
            records=[
                RecordRead.model_validate(r, from_attributes=True) for r in records
            ],
            total=total,
            page=request.page,
            page_size=request.page_size,
            has_next=has_next,
        )

    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# Schema Inspection


@router.get("/{entity_id}/schema", response_model=EntitySchemaResponse)
async def get_entity_schema(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: UUID,
) -> EntitySchemaResponse:
    """Get the dynamic schema for an entity (for UI/validation)."""
    service = CustomEntitiesService(session, role)
    try:
        # Get entity and active fields from service
        schema_result = await service.get_entity_schema(entity_id)

        # Build schema response
        return EntitySchemaResponse(
            entity=EntitySchemaInfo(
                id=str(schema_result.entity.id),
                name=schema_result.entity.name,
                display_name=schema_result.entity.display_name,
                description=schema_result.entity.description,
            ),
            fields=[
                EntitySchemaField(
                    key=f.field_key,
                    type=f.field_type,
                    display_name=f.display_name,
                    description=f.description,
                    enum_options=f.enum_options,
                )
                for f in schema_result.fields
                if f.is_active
            ],
        )
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
